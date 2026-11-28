"""Real WindowsBackend against a fake Edge UIA tree (examples/fixtures/form.html).

Shape follows Edge on Win11: the frame (Chrome_WidgetWin_1) holds browser
chrome, including an "Address and search bar" Edit, and an Edge sidebar with its
own Document. The page is a Document under the Chrome_RenderWidgetHostHWND pane
("Chrome Legacy Window"). HTML ids are AutomationIds; each <label for> is a
Text node with the same Name as its Edit.
"""

from __future__ import annotations

import asyncio

import pytest

from wincua.guard import short_error
from wincua_driver.backend.windows import (
    EDIT_TYPES,
    WindowsBackend,
    escape_send_keys,
    find_browser_document,
    is_browser_window,
    list_browser_documents,
)
from wincua_driver.types import Invoke, Semantic, Type

TITLE = "wincua form demo"
FORM_URL = "file:///C:/src/wincua/examples/fixtures/form.html"


class Info:
    def __init__(self, name="", control_type="", automation_id="", class_name="", process_id=4242):
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self.class_name = class_name
        self.process_id = process_id


class Node:
    native_find = True  # False: descendants(**criteria) unsupported, forces the children() walk

    def __init__(self, info: Info, children=None, value=None):
        self.element_info = info
        self._children = list(children or [])
        self._parent = None
        self.value = value
        self.handle = None
        for c in self._children:
            c._parent = self

    def add(self, child: "Node") -> None:
        child._parent = self
        self._children.append(child)

    def window_text(self):
        return self.element_info.name

    def children(self):
        return list(self._children)

    def parent(self):
        return self._parent

    def exists(self, timeout=0):
        return True

    def set_focus(self):
        pass

    def get_value(self):
        if self.value is None:
            raise AttributeError("no ValuePattern")
        return self.value

    def child_window(self, **kwargs):
        class _Spec:
            def exists(self, timeout=0):
                return False

        return _Spec()

    def descendants(self, depth=None, **criteria):
        if not self.native_find:
            raise TypeError("criteria not supported")
        unknown = set(criteria) - {"control_type", "title", "auto_id"}
        if unknown:
            raise TypeError(f"unsupported criteria {unknown}")
        out = []

        def walk(n, d):
            for c in n._children:
                i = c.element_info
                if (
                    criteria.get("control_type", i.control_type) == i.control_type
                    and criteria.get("title", i.name) == i.name
                    and criteria.get("auto_id", i.automation_id) == i.automation_id
                ):
                    out.append(c)
                if depth is None or d < depth:
                    walk(c, d + 1)

        walk(self, 1)
        return out


class EditNode(Node):
    def __init__(self, name, auto_id="", value=""):
        super().__init__(Info(name=name, control_type="Edit", automation_id=auto_id), value=value)
        self.writes: list[str] = []

    def set_edit_text(self, text):
        self.writes.append(text)
        self.value = text


class Page:
    """The fake form page: submit fills #result and retitles the window, like form.html."""

    def __init__(self, *, native_find: bool = True):
        Node.native_find = native_find
        self.full_name = EditNode("Full name", "fullName")
        self.email = EditNode("Email", "email")
        self.address_bar = EditNode("Address and search bar", value="edge://newtab")
        self.result = Node(Info(control_type="Group", automation_id="result"))
        self.submits = 0
        submit = Node(Info(name="Submit form", control_type="Button", automation_id="submitButton"))
        submit.invoke = self._submit
        self.submit = submit

        def label(text):
            return Node(Info(name=text, control_type="Text"), [Node(Info(name=text, control_type="Text"))])

        form = Node(
            Info(control_type="Group", automation_id="demoForm"),
            [label("Full name"), self.full_name, label("Email"), self.email, submit],
        )
        self.document = Node(
            Info(name=TITLE, control_type="Document", class_name=""),
            [Node(Info(name="wincua browser form demo", control_type="Text")), form, self.result],
            value=FORM_URL,
        )
        render = Node(Info(name="Chrome Legacy Window", control_type="Pane", class_name="Chrome_RenderWidgetHostHWND"), [self.document])
        sidebar_doc = Node(
            Info(name="Copilot", control_type="Document"),
            [EditNode("Full name", "sidebarName")],  # same Name as the form field, must be ignored
            value="https://edge.microsoft.com/sidebar",
        )
        sidebar = Node(Info(name="Edge sidebar", control_type="Pane", class_name="Chrome_RenderWidgetHostHWND"), [sidebar_doc])
        toolbar = Node(
            Info(name="App bar", control_type="ToolBar"),
            [Node(Info(name="Refresh", control_type="Button")), self.address_bar],
        )
        self.frame = Node(
            Info(name=f"{TITLE} - Microsoft\u200b Edge", control_type="Window", class_name="Chrome_WidgetWin_1"),
            [Node(Info(control_type="Pane", class_name="BrowserRootView"), [toolbar, sidebar, render])],
        )
        self.frame.handle = 0x2002

    def _submit(self):
        self.submits += 1
        name, email = self.full_name.value.strip(), self.email.value.strip()
        text = f"OK: {name} <{email}>" if name and email else "Please fill name and email."
        self.result._children = []
        self.result.add(Node(Info(name=text, control_type="Text")))
        if text.startswith("OK:"):
            self.document.element_info.name = f"{TITLE} — {text}"
            self.document.value = FORM_URL + "#submitted"
            self.frame.element_info.name = f"{TITLE} — {text} - Microsoft\u200b Edge"


def make_backend(page: Page) -> WindowsBackend:
    b = object.__new__(WindowsBackend)
    b.launch_timeout_s = 5.0
    b.control_timeout_s = 0.3
    b.allow = ["msedge.exe"]
    b._elements, b._wrappers = {}, {}
    b._window, b._content_root, b._target_title = None, None, None
    b._id_seq = 0
    b._temp_profile_dirs = []
    b._find_window = lambda target, timeout_s=None: page.frame if TITLE in target or "Edge" in target else None
    b._visible_titles = lambda limit=25: [page.frame.element_info.name]
    b._list_windows_sync = lambda: [
        {"title": page.frame.element_info.name, "process": "msedge.exe", "class_name": "Chrome_WidgetWin_1", "foreground": True}
    ]
    return b


def run(b: WindowsBackend, action):
    return asyncio.run(b.act(action))


@pytest.fixture(params=[True, False], ids=["native-find", "children-walk"])
def page(request):
    p = Page(native_find=request.param)
    yield p
    Node.native_find = True


def test_page_document_is_found_under_render_widget(page):
    assert is_browser_window(page.frame)
    docs = list_browser_documents(page.frame)
    assert [d.element_info.name for d, _ in docs] == ["Copilot", TITLE]
    assert all(parent == "Chrome_RenderWidgetHostHWND" for _, parent in docs)
    assert find_browser_document(page.frame, TITLE) is page.document


def test_observe_lists_page_controls_only(page):
    b = make_backend(page)
    obs = asyncio.run(b.observe(TITLE, ["uia", "app"]))
    names = {(r.role, r.name) for r in obs.uia}
    assert ("Edit", "Full name") in names and ("Button", "Submit form") in names
    assert ("Edit", "Address and search bar") not in names
    autos = {r.auto_id for r in obs.uia if r.auto_id}
    assert {"fullName", "email", "submitButton"} <= autos and "sidebarName" not in autos
    assert obs.app["page"] == TITLE
    assert obs.app["url"] == FORM_URL
    assert obs.app["fields"] == {"Full name": "", "Email": ""}
    assert obs.app["display"] is None


def test_fill_and_submit_by_auto_id(page):
    b = make_backend(page)
    asyncio.run(b.observe(TITLE, ["uia", "app"]))
    for auto_id, text in (("fullName", "Ada Lovelace"), ("email", "ada@example.com")):
        res = run(b, Semantic("uia.type", {"element_id": f"auto:{auto_id}", "text": text, "target": TITLE}))
        assert res.ok, res.error
        assert res.info["verified"] == text
    res = run(b, Semantic("uia.invoke", {"element_id": "auto:submitButton", "target": TITLE}))
    assert res.ok, res.error
    assert page.submits == 1
    assert page.address_bar.writes == []

    obs = asyncio.run(b.observe(TITLE, ["uia", "app"]))
    assert "OK: Ada Lovelace <ada@example.com>" in [r.name for r in obs.uia]
    assert obs.app["url"].endswith("#submitted")
    assert obs.app["fields"] == {"Full name": "Ada Lovelace", "Email": "ada@example.com"}


def test_name_ids_pick_the_edit_not_its_label(page):
    b = make_backend(page)
    b._bind_window(page.frame, title=TITLE)
    b._wrappers.clear()  # no warm cache: exercise the preferring lookup
    assert b._resolve_wrapper("name:Email", prefer=EDIT_TYPES) is page.email
    res = run(b, Type(text="ada@example.com", element_id="name:Email"))
    assert res.ok, res.error
    res = run(b, Semantic("uia.type", {"name": "Full name", "text": "Ada Lovelace"}))
    assert res.ok, res.error
    assert page.full_name.value == "Ada Lovelace"
    assert page.address_bar.writes == [] and page.email.value == "ada@example.com"
    res = run(b, Invoke(element_id="name:Submit form"))
    assert res.ok, res.error
    assert page.submits == 1


def test_missing_auto_id_falls_back_to_name(page):
    b = make_backend(page)
    b._bind_window(page.frame, title=TITLE)
    res = run(b, Semantic("uia.type", {"element_id": "auto:full_name", "name": "Full name", "text": "Ada"}))
    assert res.ok, res.error
    assert page.full_name.value == "Ada"


def test_snapshot_ids_survive_focus(page):
    b = make_backend(page)
    obs = asyncio.run(b.observe(TITLE, ["uia"]))
    snap = next(r.element_id for r in obs.uia if r.auto_id == "email")
    b._restore_and_focus = lambda window: None
    assert run(b, Semantic("focus", {"title": TITLE, "timeout_s": 1})).ok
    res = run(b, Type(text="ada@example.com", element_id=snap))
    assert res.ok, res.error
    assert page.email.value == "ada@example.com"


def test_unknown_id_is_a_short_browser_error(page):
    b = make_backend(page)
    b._bind_window(page.frame, title=TITLE)
    res = run(b, Semantic("uia.type", {"element_id": "auto:phone", "text": "123"}))
    assert not res.ok and res.error.code == "element_not_found"
    assert "Chrome_RenderWidgetHostHWND" in res.error.message
    assert "UIA tree dump" in res.error.message and "fullName" in res.error.details["tree"]
    short = short_error(res.error.message)
    assert "fullName" not in short and len(short) <= 300
    assert page.address_bar.writes == []


def test_dump_tree_shows_documents_and_values(page):
    b = make_backend(page)
    page.full_name.value = "Ada Lovelace"
    text = asyncio.run(b.dump_tree(TITLE))
    assert "Document elements: 2" in text
    assert "parent_class='Chrome_RenderWidgetHostHWND'" in text
    assert f"chosen page Document {TITLE!r}" in text
    assert "auto_id='fullName'" in text and "value='Ada Lovelace'" in text
    assert "Address and search bar" in text  # frame section, for context


def test_dump_tree_without_document_explains_accessibility():
    page = Page()
    page.frame._children[0]._children = page.frame._children[0]._children[:1]  # toolbar only
    b = make_backend(page)
    text = asyncio.run(b.dump_tree(TITLE))
    assert "Document elements: 0" in text
    assert "--force-renderer-accessibility" in text


def test_escape_send_keys():
    assert escape_send_keys("O'Brien (Jr) +1 50%") == "O'Brien {(}Jr{)} {+}1 50{%}"
    assert escape_send_keys("ada@example.com") == "ada@example.com"
