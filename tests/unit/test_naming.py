from ezconverter.orchestration.naming import (
    dedupe_names,
    output_filename,
    safe_filename,
    stem_of,
)


def test_safe_filename_strips_directories_and_controls():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\Users\\me\\x.png") == "x.png"
    assert safe_filename("a\x00b\r\nc.txt") == "a_b__c.txt"
    assert safe_filename("CON.txt").startswith("_CON")
    assert safe_filename("   ...  ") == "file"
    assert (
        safe_filename("a" * 500 + ".png").endswith(".png") and len(safe_filename("a" * 500 + ".png")) <= 140
    )
    assert safe_filename('we"ird<name>|?*.md') == "we_ird_name____.md"


def test_output_filename_replaces_extension():
    assert output_filename("holiday.photo.HEIC", "jpeg") == "holiday.photo.jpg"
    assert output_filename("backup.tar.gz", "zip") == "backup.zip"
    assert output_filename("x", "png") == "x.png"
    assert stem_of("../../a/b.c.d") == "b.c"


def test_dedupe_is_case_insensitive_and_deterministic():
    assert dedupe_names(["a.png", "A.PNG", "a.png", "b.png"]) == [
        "a.png",
        "A (1).PNG",
        "a (2).png",
        "b.png",
    ]
    assert dedupe_names(["manifest.json"], reserved=("manifest.json",)) == ["manifest (1).json"]
    assert dedupe_names(["noext", "noext"]) == ["noext", "noext (1)"]
