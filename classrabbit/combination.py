"""Conservatively verify one complete path through UCLA's linked section tree."""

from dataclasses import replace
from datetime import datetime, timezone
from html import escape
import json
import re
from urllib.parse import urlencode, urljoin

from .ucla import BASE, Document, LinkedSection, SourceError, fetch, validate_url

KINDS = {"lec": "lecture", "dis": "discussion", "lab": "laboratory",
         "sem": "seminar", "tut": "tutorial", "rec": "recitation", "qz": "quiz"}


def models_in(markup):
    models = {}
    for match in re.finditer(r'AddToCourseData\(\s*"([^"\n]+)"\s*,\s*', markup):
        try:
            model, _ = json.JSONDecoder().raw_decode(markup[match.end():])
        except ValueError as exc:
            raise SourceError("Linked-section metadata is unreadable.") from exc
        if not isinstance(model, dict) or model.get("Path") != match[1] or match[1] in models:
            raise SourceError("Linked-section metadata is ambiguous.")
        models[match[1]] = model
    return models


def verify_model(model, identity):
    expected = {"Term": identity["term_cd"], "SubjectAreaCode": identity["subj_area_cd"],
                "CatalogNumber": identity["crs_catlg_no"]}
    if any(str(model.get(k, "")).strip() != v.strip() for k, v in expected.items()):
        raise SourceError("Linked sections belong to a different course or term.")
    depth = str(model.get("SequenceNumber", ""))
    if not re.fullmatch(r"[0-3]", depth) or model.get("IsRoot") is not False:
        raise SourceError("Required-section depth is unknown.")
    return int(depth)


def column(row, name):
    nodes = [n for n in row.all() if name in n.attrs.get("class", "").split()]
    if len(nodes) != 1:
        raise SourceError(f"Required section has an unreadable {name}.")
    return nodes[0]


def status_counts(text):
    if re.search(r"-\s*\d", text):
        raise SourceError("Required section has negative seat counts.")
    match = re.match(r"^(Open|Closed|Full|Waitlist(?:ed)?|Cancel(?:led|ed)?)\b", text, re.I)
    if not match:
        raise SourceError(f"Required section has an unknown status: {text!r}")
    raw = match[1].lower()
    status = ("open" if raw == "open" else "cancelled" if raw.startswith("cancel")
              else "waitlist" if raw.startswith("waitlist") else "closed")
    enrolled = re.search(r"([\d,]+) of ([\d,]+) Enrolled", text, re.I)
    left = re.search(r"([\d,]+) Spots? Left", text, re.I)
    full = re.search(r"Class Full\s*\(([\d,]+)\)", text, re.I)
    def number(value):
        return int(value.replace(",", ""))
    capacity = number(enrolled[2]) if enrolled else number(full[1]) if full else None
    available = number(left[1]) if left else (capacity - number(enrolled[1]) if enrolled else 0 if status == "closed" else None)
    if left and enrolled and available != capacity - number(enrolled[1]):
        raise SourceError("Required section has inconsistent enrolled and remaining counts.")
    if (available is not None and available < 0) or (capacity is not None and available is not None and available > capacity):
        raise SourceError("Required section has inconsistent seat counts.")
    if status == "open" and (available is None or available <= 0 or capacity is None):
        raise SourceError("Required section says open without a positive verified seat count.")
    return status, available, capacity


def section_kind(label):
    prefix = label.split(" ", 1)[0].lower()
    if prefix not in KINDS:
        raise SourceError(f"Unsupported required section type: {label!r}")
    return KINDS[prefix]


def assess_combination(snapshot, listing, fetch_summary=None):
    """An available result requires a positive lecture and every required linked level.

    Parent paths and decreasing SequenceNumber values define valid alternatives.
    Mixed sibling types, missing children, parser failures, and source disagreements
    remain unknown. A standalone discussion link never implies its lecture is open.
    """
    linked = []
    if snapshot.status != "open" or not snapshot.available:
        return replace(snapshot, combination_status="blocked",
                       combination_reason="The watched section is not open; no complete seat combination is available.")
    fetch_summary = fetch_summary or fetch
    identity = validate_url(snapshot.source_url)
    try:
        doc = Document(listing)
        models = models_in(listing)
        rows = [n for n in doc.root.all() if "primary-row" in n.attrs.get("class", "").split()
                and n.attrs.get("id", "").startswith(snapshot.class_id + "_")]
        if len(rows) != 1:
            raise SourceError("Watch the lecture section to verify its required discussion/lab combination.")
        row = rows[0]
        path = row.attrs["id"]
        model = models.get(path)
        if model is None:
            raise SourceError("The lecture's required-section metadata is missing.")
        depth = verify_model(model, identity)
        primary_status, _, _ = status_counts(column(row, "statusColumn").clean())
        if primary_status != snapshot.status:
            raise SourceError("The lecture listing and detail page disagree; a complete seat combination is unverified.")
        groups = [n for n in doc.root.all() if "class-title" in n.attrs.get("class", "").split()
                  and any(child is row for child in n.all())]
        if len(groups) != 1:
            raise SourceError("Required-section instructions could not be associated with this lecture.")
        notes = " ".join(n.clean() for n in groups[0].all() if "class-note" in n.attrs.get("class", "").split())
        required = [word.lower() for word in re.findall(r"\(\d+\)\s*an?\s+(\w+)", notes, re.I)]
        primary_kind = section_kind(snapshot.section)
        if required and (required[0] != primary_kind or len(required) != depth + 1):
            raise SourceError("Required-section instructions and the section hierarchy disagree.")
        if depth and not required:
            raise SourceError("The source does not identify all required section types.")
        requests = 0
        visited = {snapshot.class_id}

        def walk(parent_model, parent_id, level):
            nonlocal requests
            parent_depth = verify_model(parent_model, identity)
            if parent_depth == 0:
                return [[parent_id]]
            requests += 1
            if requests > 12:
                raise SourceError("The linked-section tree exceeds this prototype's verification limit.")
            url = BASE + "Results/GetCourseSummary?" + urlencode({
                "model": json.dumps(parent_model), "FilterFlags": json.dumps({"advanced": "y"}),
                "IsMultiListedTitles": "false"})
            markup = fetch_summary(url)
            child_doc = Document(markup)
            child_models = models_in(markup)
            children = child_doc.by_id(parent_model["Path"] + "-children")
            child_rows = [n for n in children.children if hasattr(n, "attrs")
                          and "data_row" in n.attrs.get("class", "").split()]
            if not child_rows:
                raise SourceError("A required linked-section group is empty or missing.")
            parsed = []
            for child in child_rows:
                child_path = child.attrs.get("id", "")
                child_id = child_path.split("_")[0]
                if (not re.fullmatch(r"\d{9}", child_id) or child_id in visited
                        or child_path != child_id + "_" + parent_model["Path"]):
                    raise SourceError("A linked section is associated with an unexpected parent.")
                visited.add(child_id)
                if len(visited) > 100:
                    raise SourceError("Too many linked sections to verify safely.")
                child_model = child_models.get(child_path)
                if child_model is None or verify_model(child_model, identity) != parent_depth - 1:
                    raise SourceError("A required child section has missing or inconsistent metadata.")
                anchor = [n for n in column(child, "sectionColumn").all()
                          if n.tag == "a" and "ClassDetail?" in n.attrs.get("href", "")]
                if len(anchor) != 1:
                    raise SourceError("Required section identity is missing.")
                child_url = urljoin(BASE, anchor[0].attrs["href"])
                child_identity = validate_url(child_url)
                if (child_identity["class_id"] != child_id or any(child_identity[k].strip() != identity[k].strip()
                        for k in ["term_cd", "subj_area_cd", "crs_catlg_no"])):
                    raise SourceError("A linked section belongs to a different course or term.")
                label = anchor[0].clean()
                kind = section_kind(label)
                if required and kind != required[level]:
                    raise SourceError("A linked section has an unexpected required type.")
                status, available, capacity = status_counts(column(child, "statusColumn").clean())
                day_nodes = [n.clean() for n in child.all() if "dayColumn" in n.attrs.get("class", "").split()]
                time_nodes = [n.clean() for n in child.all() if "timeColumn" in n.attrs.get("class", "").split()]
                info = LinkedSection(child_id, parent_id, label, kind, status, available, capacity,
                                     child_url, parent_depth > 1, " ".join(day_nodes), " ".join(time_nodes))
                linked.append(info)
                parsed.append((info, child_model))
            if len({info.kind for info, _ in parsed}) != 1:
                raise SourceError("Mixed linked-section requirements need manual verification.")
            combinations = []
            for info, child_model in parsed:
                if info.status == "open" and info.available and info.available > 0:
                    combinations.extend([[parent_id] + tail for tail in walk(child_model, info.class_id, level + 1)])
            return combinations

        combinations = walk(model, snapshot.class_id, 1)
        if combinations:
            return replace(snapshot, combination_status="available", linked_sections=linked,
                           open_combinations=combinations,
                           combination_reason="A complete required section set reports seats. Confirm restrictions and your enrollment pass in MyUCLA.")
        reason = "No complete lecture/discussion/lab seat combination is available."
        if depth == 1 and linked and all(s.kind == "discussion" for s in linked):
            reason = "Lecture seats are reported, but every required discussion is closed."
        return replace(snapshot, combination_status="blocked", combination_reason=reason, linked_sections=linked)
    except SourceError as exc:
        return replace(snapshot, combination_status="unknown", linked_sections=linked,
                       combination_reason=f"Required sections could not be verified: {exc}")


def check_summary_combination(metadata, listing, fetch_summary=None):
    """Refresh primary and child counts via public summaries, keeping metadata separate.

    These HTTP responses have been observed with no-cache, but UCLA publishes no
    underlying freshness guarantee for this endpoint. No realtime claim is made.
    """
    fetch_summary = fetch_summary or fetch
    identity = validate_url(metadata.source_url)
    doc = Document(listing)
    rows = [n for n in doc.root.all() if "primary-row" in n.attrs.get("class", "").split()
            and n.attrs.get("id", "").startswith(metadata.class_id + "_")]
    if len(rows) != 1:
        raise SourceError("The watched lecture cannot be associated with its required sections.")
    groups = [n for n in doc.root.all() if "class-title" in n.attrs.get("class", "").split()
              and any(child is rows[0] for child in n.all())]
    if len(groups) != 1:
        raise SourceError("The watched lecture has ambiguous course metadata.")
    root = models_in(listing).get(groups[0].attrs.get("id"))
    if not root or root.get("IsRoot") is not True:
        raise SourceError("Course-summary metadata is missing.")
    expected = {"Term": identity["term_cd"], "SubjectAreaCode": identity["subj_area_cd"],
                "CatalogNumber": identity["crs_catlg_no"]}
    if any(str(root.get(k, "")).strip() != v.strip() for k, v in expected.items()):
        raise SourceError("Course-summary metadata belongs to a different course or term.")
    url = BASE + "Results/GetCourseSummary?" + urlencode({
        "model": json.dumps(root), "FilterFlags": json.dumps({"advanced": "y"}), "IsMultiListedTitles": "false"})
    markup = fetch_summary(url)
    primary = [n for n in Document(markup).root.all() if "primary-row" in n.attrs.get("class", "").split()
               and n.attrs.get("id", "").startswith(metadata.class_id + "_")]
    if len(primary) != 1:
        raise SourceError("The refreshed summary did not return the exact watched lecture.")
    anchors = [n for n in column(primary[0], "sectionColumn").all()
               if n.tag == "a" and "ClassDetail?" in n.attrs.get("href", "")]
    if len(anchors) != 1:
        raise SourceError("The refreshed lecture identity is missing.")
    observed = validate_url(urljoin(BASE, anchors[0].attrs["href"]))
    if any(observed[k].strip() != identity[k].strip() for k in identity):
        raise SourceError("The refreshed summary belongs to a different lecture.")
    status, available, capacity = status_counts(column(primary[0], "statusColumn").clean())
    snapshot = replace(metadata, status=status, available=available, capacity=capacity,
                       checked_at=datetime.now(timezone.utc).isoformat(), source="ucla_public_summary",
                       source_notice="Public section-summary counts; underlying update delay is unverified. Detail-page metadata notice: " + metadata.source_notice)
    # The summary returns a primarySection fragment rather than a course-title wrapper.
    wrapped = '<div class="class-title" id="' + escape(str(root["Path"]), quote=True) + '">' + markup + '</div>'
    return assess_combination(snapshot, wrapped, fetch_summary)
