"""Read-only adapter for UCLA's public, hourly-cached class detail pages.

No student credentials or private enrollment endpoints are used.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "https://sa.ucla.edu/ro/Public/SOC/"
ENROLL_URL = "https://my.ucla.edu/directLink.aspx?featureID=203"


class SourceError(Exception):
    """A source response cannot be trusted as a section observation."""


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def all(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.all()

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        return " ".join(
            child.text() if isinstance(child, Node) else child
            for child in self.children
        )

    def clean(self):
        return " ".join(self.text().split())


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def by_id(self, identifier):
        nodes = [n for n in self.root.all() if n.attrs.get("id") == identifier]
        if len(nodes) != 1:
            raise SourceError(f"Expected one {identifier!r} element; source may have changed.")
        return nodes[0]


def validate_url(url):
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise SourceError("Invalid public UCLA class link.") from exc
    if (parts.scheme != "https" or parts.netloc.lower() != "sa.ucla.edu"
            or parts.username or parts.password
            or parts.path.lower() != "/ro/public/soc/results/classdetail"):
        raise SourceError("Use a public HTTPS UCLA Schedule of Classes ClassDetail URL.")
    query = parse_qs(parts.query)
    required = {"term_cd", "subj_area_cd", "crs_catlg_no", "class_id", "class_no"}
    if any(len(query.get(k, [])) != 1 or not query[k][0].strip() for k in required):
        raise SourceError("The class link is missing a unique term, subject, course, class ID, or section.")
    if not re.fullmatch(r"\d{2}[FWS12]", query["term_cd"][0]):
        raise SourceError("Invalid UCLA term code.")
    if not re.fullmatch(r"\d{9}", query["class_id"][0]):
        raise SourceError("Invalid UCLA class ID.")
    return {k: query[k][0] for k in required}


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if (parts.scheme != "https" or parts.netloc.lower() != "sa.ucla.edu"
                or not parts.path.lower().startswith("/ro/public/soc/")):
            raise SourceError("UCLA redirected away from the public schedule; no availability recorded.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    request = Request(url, headers={
        "User-Agent": "Classrabbit-Feasibility/0.1 (read-only public schedule checker)",
        "Accept": "text/html, application/json",
        "X-Requested-With": "XMLHttpRequest",
    })
    try:
        with build_opener(PublicRedirects()).open(request, timeout=25) as response:
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise SourceError("Unexpectedly large source response.")
            return content.decode(response.headers.get_content_charset() or "utf-8")
    except HTTPError as exc:
        raise SourceError(f"UCLA returned HTTP {exc.code}; availability is unknown.") from exc
    except (URLError, TimeoutError, UnicodeError, OSError) as exc:
        raise SourceError(f"Could not read UCLA's public schedule: {exc}") from exc


@dataclass(frozen=True)
class Snapshot:
    school: str
    term: str
    class_id: str
    course: str
    section: str
    status: str
    available: int | None
    capacity: int | None
    waitlist: str
    restrictions: str
    checked_at: str
    source_notice: str
    source_url: str
    enrollment_url: str = ENROLL_URL
    source: str = "ucla_public_detail"
    realtime: bool = False

    def as_dict(self):
        return asdict(self)


def parse_detail(markup, url):
    identity = validate_url(url)
    doc = Document(markup)
    class_info = doc.by_id("class_id_textbook").clean()
    actual_id = re.search(r"Class ID:\s*(\d{9})(?!\d)", class_info)
    if not actual_id or actual_id[1] != identity["class_id"]:
        raise SourceError("Returned class ID does not match the requested section.")
    term = identity["term_cd"]
    season = {"W": "Winter", "S": "Spring", "F": "Fall", "1": "Summer", "2": "Summer"}[term[-1]]
    term_text = doc.by_id("term_display").clean()
    if season not in term_text or f"20{term[:2]}" not in term_text:
        raise SourceError("Returned term does not match the requested term.")
    course = doc.by_id("subject_class").clean()
    section_match = re.search(r"(?:Lecture|Discussion|Laboratory|Seminar|Section):\s*(.*?)\s*Class ID:", class_info)
    section = section_match[1] if section_match else class_info.split("Class ID:")[0].strip()
    rows = [n for n in doc.by_id("enrl_mtng_info").all() if n.tag == "tr"]
    cells = next(([n for n in row.children if isinstance(n, Node) and n.tag == "td"]
                  for row in rows if any(isinstance(n, Node) and n.tag == "td" for n in row.children)), [])
    if len(cells) < 2:
        raise SourceError("Enrollment table is missing; availability is unknown.")
    raw_status = cells[0].clean()
    match = re.match(r"^(Open|Closed|Waitlist(?:ed)?|Cancel(?:led|ed)?|Full)\b", raw_status, re.I)
    if not match:
        raise SourceError(f"Unrecognized status: {raw_status!r}; no availability recorded.")
    word = match[1].lower()
    status = ("open" if word == "open" else "cancelled" if word.startswith("cancel")
              else "waitlist" if word.startswith("waitlist") else "closed")
    count = re.search(r"([\d,]+)\s+of\s+([\d,]+)\s+Left\b", raw_status, re.I)
    available, capacity = (int(count[1].replace(",", "")), int(count[2].replace(",", ""))) if count else (None, None)
    if available is not None and (available > capacity or (status == "open" and available == 0)):
        raise SourceError("Inconsistent seat counts; no availability recorded.")
    if status == "open" and count is None:
        raise SourceError("Open status without a recognizable seat count; no availability recorded.")
    notices = [n.clean() for n in doc.root.all()
               if n.tag == "div" and "message" in n.attrs.get("class", "").split()
               and "status" in n.clean().lower()]
    if not notices:
        raise SourceError("Source freshness notice is missing; source needs review.")
    restriction_rows = [n for n in doc.by_id("enrollment_info").all() if n.tag == "tr"]
    restriction_cells = next(([n for n in row.children if isinstance(n, Node) and n.tag == "td"]
                              for row in restriction_rows if any(isinstance(n, Node) and n.tag == "td" for n in row.children)), [])
    restrictions = restriction_cells[1].clean() if len(restriction_cells) > 1 else "Unknown"
    return Snapshot("ucla", term, identity["class_id"], course, section, status,
                    available, capacity, cells[1].clean(), restrictions,
                    datetime.now(timezone.utc).isoformat(), " ".join(notices), url)


def find_sections(term, subject, course):
    """Find numeric catalog courses. More complex catalog codes use a ClassDetail URL."""
    if not re.fullmatch(r"\d{2}[FWS12]", term) or not re.fullmatch(r"\d{1,4}", course):
        raise SourceError("Use a term such as 27W and a numeric course such as 31; otherwise supply --url.")
    catalog = course.zfill(4).ljust(8)
    model = {"subj_area_cd": subject.upper().strip(), "crs_catlg_no": catalog,
             "search_by": "Subject", "term_cd": term, "ActiveEnrollmentFlag": "n", "HasData": "False"}
    flags = dict.fromkeys(["enrollment_status", "meet_days", "start_time", "end_time", "meet_locations",
                          "meet_units", "instructor", "class_career", "impacted", "enrollment_restrictions",
                          "enforced_requisites", "individual_studies", "summer_session"])
    flags["advanced"] = "y"
    url = BASE + "Results/GetCourseTitlesPage?" + urlencode({
        "search_by": "subject", "model": json.dumps(model), "filterFlags": json.dumps(flags), "isFilter": "false"})
    try:
        data = json.loads(fetch(url))
        markup = data["ClassPartialViewData"]
        if not isinstance(markup, str):
            raise ValueError("Missing listing markup")
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("UCLA did not return the expected course listing.") from exc
    doc = Document(markup)
    result = {}
    for node in doc.root.all():
        href = node.attrs.get("href", "")
        if node.tag != "a" or "Results/ClassDetail?" not in href:
            continue
        detail_url = urljoin(BASE, href)
        identity = validate_url(detail_url)
        if (identity["term_cd"] != term or identity["subj_area_cd"].strip().upper() != subject.strip().upper()
                or identity["crs_catlg_no"].strip() != catalog.strip()):
            raise SourceError("UCLA returned a different course than requested.")
        result[identity["class_id"]] = {"class_id": identity["class_id"], "section": node.clean(), "url": detail_url}
    if not result:
        raise SourceError("No sections found. Verify the school term, subject abbreviation, and course number.")
    return list(result.values())


def check(url):
    validate_url(url)
    return parse_detail(fetch(url), url)
