from pathlib import Path
import shutil
import subprocess


def test_webchat_js_syntax_valid():
    node = shutil.which("node")
    if not node:
        return
    subprocess.run([node, "--check", "src/gateway/static/js/webchat.js"], check=True)


def test_no_upload_library_or_file_selector_frontend_residue():
    js = Path("src/gateway/static/js/webchat.js").read_text(encoding="utf-8")
    html = Path("src/gateway/templates/webchat.html").read_text(encoding="utf-8")
    forbidden = [
        "My Uploads",
        'data-action="my-uploads"',
        "fileSelector",
        "fileDropdown",
        "selectedFileIndex",
        "filesLoaded",
        "showFileSelector",
        "hideFileSelector",
        "navigateFileList",
        "loadFilesForSelector",
        "showMyUploads",
        "toggleMyUploads",
        "uploadedFiles",
        "@file_",
        "/api/files/list",
        "/api/context/files",
        "refreshFileList",
        "processMessageImages",
        "fetch('')",
        "const  =",
        "let  =",
        "async function ()",
    ]
    for token in forbidden:
        assert token not in js
        assert token not in html
    assert "pendingAttachments" in js
    assert "attachments: attachmentIds" in js
    assert "api/files/upload?session_id=" in js
    assert "api/files/parse?session_id=" in js


def test_webchat_js_was_not_replaced_by_minimal_stub():
    js = Path("src/gateway/static/js/webchat.js").read_text(encoding="utf-8")
    html = Path("src/gateway/templates/webchat.html").read_text(encoding="utf-8")
    corpus = js + "\n" + html
    line_count = len(js.splitlines())
    assert line_count > 2000, "webchat.js appears to have been replaced by a minimal stub."

    required_tokens = [
        "themeToggle",
        "toggleSidebar",
        "statsButton",
        "refreshSessions",
        "recentSessionsList",
        "tokenCount",
        "costDisplay",
        "typing",
        "streamingContent",
        "skillDropdown",
        "skillList",
        "/api/skills",
        "/api/sessions",
        "settingsPanel",
        "closeSettings",
        "saveSettings",
        "fileViewerPanel",
        "server-files",
    ]
    for token in required_tokens:
        assert token in corpus, f"Expected existing WebChat feature token to remain: {token}"


def test_required_one_shot_tokens_present():
    js = Path("src/gateway/static/js/webchat.js").read_text(encoding="utf-8")
    html = Path("src/gateway/templates/webchat.html").read_text(encoding="utf-8")
    assert 'id="clearButton"' in html or 'clear-button' in html
    assert "pendingAttachments" in js
    assert "renderPendingAttachments" in js
    assert "shouldParseAttachment" in js
    assert "api/files/upload?session_id=" in js
    assert "api/files/parse?session_id=" in js
    assert "attachments: attachmentIds" in js
    assert "'[attachment]'" in js or '"[attachment]"' in js
