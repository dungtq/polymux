from pathlib import Path

import pytest

from pm.security.mask import MASK, configure, mask, render_pane, strip_ansi

FX = Path(__file__).parent / "fixtures" / "mask"


def fx(name: str) -> str:
    return (FX / name).read_text()


def test_strip_sgr_csi_osc_and_ctrl():
    assert strip_ansi("\x1b[1;31mred\x1b[0m \x1b[2K\x1b[?25l ok\r\x07\x00") == "red  ok"
    assert strip_ansi("a\x1b]0;title\x07b\x1b]2;t\x1b\\c") == "abc"
    assert strip_ansi("a\tb\nc") == "a\tb\nc"
    assert strip_ansi("x\x1b(By\x1b=z") == "xyz"


def test_osc8_stripped_keeps_text():
    out = strip_ansi(fx("osc8.txt"))
    assert "link text" in out and "bel" in out and "\x1b" not in out and "\x07" not in out
    assert "https://" not in out  # hyperlink target is dropped


def test_unterminated_osc_does_not_eat_next_line():
    assert strip_ansi("a\x1b]8;;http://x\nnext") == "a\nnext"


def test_sgr_mid_token_masked():
    out = render_pane(fx("sgr_mid_token.txt"))
    assert "ABCDEFGH" not in out and "ghp_" not in out and MASK in out
    assert out.endswith(" ok\n")


def test_pem_block_multiline():
    out = render_pane(fx("pem.txt"))
    assert "b3BlbnNzaC1" not in out and "fakefake" not in out and "END OPENSSH" not in out
    assert out.startswith("$ cat key.pem\n") and out.endswith("$ ls\n")


def test_pem_truncated_masks_to_end():
    out = render_pane("-----BEGIN RSA PRIVATE KEY-----\nMIIEabcdef\nmore")
    assert "MIIE" not in out and "more" not in out


def test_export_lines():
    out = render_pane(fx("exports.txt"))
    assert "sk-proj" not in out and "p@ss" not in out and "wJalr" not in out
    assert "export OPENAI_API_KEY=" in out and "export DB_PASSWORD=" in out
    assert "export PATH=/usr/bin:/bin" in out and "EDITOR=nvim" in out
    assert "TOKEN_FILE=/home/me/.config/pm/token" in out


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuv",
        "ghp_" + "A1b2C3d4E5" * 3,
        "github_pat_11ABCDEFG0abcdefghijkl_mnop",
        "xoxb-123456789012-abcdefghij",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        "tk_abcdefghijklmnopqrstuvwxyz012",
    ],
)
def test_token_formats(secret):
    out = render_pane(f"value {secret} end")
    assert secret not in out and "end" in out


def test_bearer_and_url_creds_and_prose_key():
    out = render_pane("curl -H 'Authorization: Bearer abcDEF123456789xyz' https://bob:s3cr3tpw@git.example.test/x")
    assert "abcDEF" not in out and "s3cr3tpw" not in out and "bob:" not in out
    assert "git.example.test/x" in out
    out = render_pane("the password is 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b")
    assert "9f86d08" not in out


def test_hard_wrapped_token():
    wrapped = "key: sk-abcdefghijklmnopqrstuv\nWXYZ0123456789abcdef\nnext line"
    out = render_pane(wrapped)
    assert "WXYZ0123" not in out and "abcdefghij" not in out
    assert out.endswith("next line")


def test_joined_wrapped_token():
    assert "abcdefghij" not in render_pane("sk-" + "abcdefghij" * 6)


def test_false_positives_survive():
    text = fx("prose.txt")
    assert render_pane(text) == text
    assert mask("git rev-parse HEAD -> 3f786850e387550fdab836ed7e6dc881de23001b") == (
        "git rev-parse HEAD -> 3f786850e387550fdab836ed7e6dc881de23001b"
    )


def test_literal_registration():
    configure(literals=["my-very-own-pm-token-value"])
    try:
        assert "very-own" not in render_pane("x my-very-own-pm-token-value y")
    finally:
        configure()


def test_idempotent():
    once = render_pane("export API_KEY=abcdefgh12345678")
    assert render_pane(once) == once
