"""Проверка окружения: что нужно конвейеру и чего не хватает.

Ставить пакеты сами не берёмся: системный пакет требует прав, а команда,
запускающая от чужого имени `sudo` без спросу, — это то, чего в чужой
машине быть не должно. Поэтому говорим ровно и коротко: чего нет, зачем оно
и какой строкой ставится **на этой** системе.
"""
import os
import shutil
import sys

from .lang import T

# Чем ставят пакеты. Порядок важен: в системе бывает и apt, и snap, берём
# первое найденное.
LINUX_PM = (
    ("apt-get", "sudo apt install {apt}"),
    ("dnf", "sudo dnf install {rpm}"),
    ("yum", "sudo yum install {rpm}"),
    ("pacman", "sudo pacman -S {arch}"),
    ("zypper", "sudo zypper install {rpm}"),
    ("emerge", "sudo emerge {gentoo}"),
    ("apk", "sudo apk add {alpine}"),
)

POPPLER = {"apt": "poppler-utils", "rpm": "poppler-utils", "arch": "poppler",
           "gentoo": "app-text/poppler", "alpine": "poppler-utils",
           "brew": "poppler", "win": "poppler"}


def _install_line(pkg):
    """Строка установки для этой системы."""
    if sys.platform == "darwin":
        if shutil.which("brew"):
            return f"brew install {pkg['brew']}"
        return ("сначала поставьте Homebrew (https://brew.sh), потом: "
                f"brew install {pkg['brew']}")
    if os.name == "nt" or sys.platform.startswith("win"):
        for exe, line in (("scoop", "scoop install {win}"),
                          ("choco", "choco install {win}"),
                          ("winget", "winget install --id oschwartz10612.Poppler")):
            if shutil.which(exe):
                return line.format(**pkg)
        return ("поставьте Scoop (https://scoop.sh), потом: "
                f"scoop install {pkg['win']}")
    for exe, line in LINUX_PM:
        if shutil.which(exe):
            return line.format(**pkg)
    return f"поставьте пакет {pkg['apt']} средствами вашего дистрибутива"


def check(log=print, agent="claude"):
    """Что есть и чего нет. Возвращает число недостающего обязательного.

    Обязательное и необязательное разведены нарочно: без агента нельзя
    ничего, а без poppler — только читать pdf. Свалив это в один список, мы
    отпугнули бы человека, которому нужен один epub.
    """
    bad = opt_bad = 0

    def line(ok, what, why, how="", need=True):
        nonlocal bad, opt_bad
        log(f"  {'+' if ok else '-'} {what:22s} {why}")
        if not ok:
            if need:
                bad += 1
            else:
                opt_bad += 1
            if how:
                log(f"      {T('doc_install')}: {how}")

    log("  " + T("doc_need"))
    line(sys.version_info >= (3, 9), f"python {sys.version_info.major}."
         f"{sys.version_info.minor}", T("doc_python"),
         "https://www.python.org/downloads/")
    who = {"claude": "claude", "agy": "agy", "codex": "codex"}.get(agent)
    if who:
        line(bool(shutil.which(who)), who, T("doc_agent", agent),
             T("doc_agent_how", agent))

    log("")
    log("  " + T("doc_opt"))
    line(bool(shutil.which("pdftotext")), "pdftotext", T("doc_pdftotext"),
         _install_line(POPPLER), need=False)
    line(bool(shutil.which("pdfimages")), "pdfimages", T("doc_pdfimages"),
         _install_line(POPPLER), need=False)
    try:
        import charset_normalizer          # noqa: F401
        ok = True
    except ImportError:
        ok = False
    line(ok, "charset-normalizer", T("doc_charset"),
         f"{os.path.basename(sys.executable)} -m pip install charset-normalizer",
         need=False)

    # Где искать свои промпты — вопрос, на который иначе отвечают гаданием:
    # папка настроек называется по-разному на трёх системах.
    from .cli import prompt_roots
    log("")
    log("  " + T("doc_prompts"))
    for r in prompt_roots():
        short = r.replace(os.path.expanduser("~"), "~")
        log(f"  {'+' if os.path.isdir(r) else '-'} {short}")

    log("")
    log("  " + (T("doc_ok") if not bad else T("doc_bad", bad)))
    if opt_bad:
        log("  " + T("doc_opt_bad", opt_bad))
    return bad
