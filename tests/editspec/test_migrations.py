import pytest

from studio.editspec.migrations import MigrationError, load_migrated, migrate_payload
from studio.editspec.schema import Canvas, EditSpec, Timebase


def payload(version="2.2.0"):
    return EditSpec(
        id="p",
        timebase=Timebase(num=24),
        canvas=Canvas(width=1080, height=1350),
    ).model_dump(mode="json", by_alias=True) | {"spec_version": version}


def test_current_version_is_identity_without_mutation():
    source = payload()
    result = migrate_payload(source)
    assert result == source
    assert result is not source


def test_internal_v2_dev_migration_adds_new_fields():
    source = payload("2.0.0-dev")
    source.pop("captions")
    source.pop("revision")
    result = migrate_payload(source)
    assert result["spec_version"] == "2.2.0"
    assert result["captions"] == []
    assert result["revision"] == 1
    assert result["motion_phrases"] == []
    assert load_migrated(source).spec_version == "2.2.0"


def test_200_migration_adds_motion_phrases():
    source = payload("2.0.0")
    source.pop("motion_phrases")
    result = migrate_payload(source)
    assert result["spec_version"] == "2.2.0"
    assert result["motion_phrases"] == []


def test_v1_is_explicitly_rejected():
    with pytest.raises(MigrationError, match="v1 EditSpec 不得迁入"):
        migrate_payload({"spec_version": "1.0.0"})


def test_unknown_future_version_is_rejected():
    with pytest.raises(MigrationError, match="没有"):
        migrate_payload({"spec_version": "2.9.0"})
