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
        "fileList",
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
