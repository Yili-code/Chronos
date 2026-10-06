import pytest
from chronos import main
from chronos.db import Database
from chronos.note_commands import note_command, text_pages
from test_note_record import note


def test_pages_preserve_unicode_and_all_content():
    text = "課程🙂" * 3000
    pages = text_pages(text)
    assert "".join(pages) == text
    assert all(len(page.encode("utf-16-le")) // 2 <= 3000 for page in pages)


@pytest.mark.asyncio
async def test_commands_use_note_repository_without_ai(tmp_path, monkeypatch):
    db = Database(tmp_path / "notes.db")
    db.initialize()
    monkeypatch.setattr(main, "db", db)
    assert "尚無" in await main.handle_message("/notes")
    db.save_study_note(note(markdown="甲" * 4000))
    listing = await main.handle_message("/notes OS")
    assert "a" * 64 in listing
    assert "2026-" not in listing
    assert "尚無" in await main.handle_message("/notes other")
    first = await main.handle_message("/note " + "a" * 64)
    second = await main.handle_message("/note " + "a" * 64 + " 2")
    assert first.count("甲") == 3000
    assert second.count("甲") == 1000
    assert "超出範圍" in note_command(db, "note " + "a" * 64 + " 3")
    assert "找不到" in note_command(db, "note " + "b" * 64)
    assert "用法" in note_command(db, "note ../bad")
