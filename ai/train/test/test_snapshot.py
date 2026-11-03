"""Snapshot copy/failure boundaries only; no video decode, training, ROS or SDK."""
import fcntl
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ai.train import snapshot as s


@pytest.fixture
def collection(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'PROJECT', tmp_path)
    monkeypatch.setattr(s, 'SNAPSHOTS', tmp_path / 'snapshots')
    monkeypatch.setattr(s, 'validate_output', lambda root, repo: {'test': 'file-copy-only'})
    c = tmp_path / 'collection'; root = c / 'success'
    folder = root / 'meta/recorder_timing/episode-000000'
    folder.mkdir(parents=True)
    (c / '.collection.lock').touch()
    (c / 'receipts').mkdir(); (c / 'receipts/a.json').write_text('{}')
    (c / 'collection.json').write_text(json.dumps({'directory': str(c), 'attempts': [
        {'attempt_id': 'a', 'status': 'SUCCESS', 'dataset_episode_index': 0}]}))
    (c / 'correction').mkdir(); (c / 'correction/secret').write_text('excluded')
    info = {'codebase_version': 'v3.0', 'fps': 20, 'robot_type': 'indy7', 'total_episodes': 1,
            'total_frames': 2, 'features': {}, 'data_path': 'data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet',
            'video_path': 'videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4'}
    (root / 'meta/info.json').write_text(json.dumps(info))
    (root / 'meta/stats.json').write_text('{}')
    (root / 'meta/tasks.parquet').write_bytes(b'tasks')
    (root / 'data/chunk-000').mkdir(parents=True)
    (root / 'data/chunk-000/file-000.parquet').write_bytes(b'data')
    (root / 'meta/episodes/chunk-000').mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{'episode_index': 0, 'length': 2, 'dataset_from_index': 0,
        'dataset_to_index': 2, 'data/chunk_index': 0, 'data/file_index': 0}]),root / 'meta/episodes/chunk-000/file-000.parquet')
    (folder / 'episode.json').write_text(json.dumps({'status': 'saved', 'outcome': 'success', 'dataset_repo_id': 'local/test'}))
    (folder / 'frames.jsonl').write_text('{}\n{}\n')
    return c


def test_copy_is_independent_and_keeps_provenance(collection):
    before = s.inventory(collection / 'success')
    result = s.create_snapshot([collection], 'first', 1)
    out = Path(result['snapshot_path'])
    for file in before:
        a, b = collection / 'success' / file, out / file
        assert a.read_bytes() == b.read_bytes()
        assert a.stat().st_ino != b.stat().st_ino
        assert not b.is_symlink()
    assert not (out / 'correction').exists()
    assert result['episodes'][0]['origin']['attempt_id'] == 'a'
    assert result['episode_count'] == 1
    assert s.inventory(collection / 'success') == before
    (collection / 'success/meta/stats.json').write_text('new source value')
    assert (out / 'meta/stats.json').read_text() == '{}'


def test_snapshot_as_input_keeps_original_origin(collection):
    first = s.create_snapshot([collection], 'first', 1)
    second = s.create_snapshot([Path(first['snapshot_path'])], 'second', 1)
    assert first['snapshot_id'] == second['inputs'][0]['snapshot_id']
    assert second['episodes'][0]['origin'] == first['episodes'][0]['origin']


def test_wrong_count_and_existing_name_do_not_publish(collection):
    with pytest.raises(ValueError, match='Expected 10.*found 1'):
        s.create_snapshot([collection], 'first', 10)
    s.create_snapshot([collection], 'first', 1)
    with pytest.raises(FileExistsError):s.create_snapshot([collection], 'first', 1)


def test_source_change_is_rejected(collection, monkeypatch):
    original = s.copy_file
    def changing(src, dst, expected):
        original(src, dst, expected)
        if src.name == 'stats.json':src.write_text('{"changed":true}')
    monkeypatch.setattr(s, 'copy_file', changing)
    with pytest.raises(ValueError, match='Source changed'):
        s.create_snapshot([collection], 'changed', 1)
    assert not (s.SNAPSHOTS / 'changed').exists()
    assert not list(s.SNAPSHOTS.glob('.*.copying-*'))


def test_failure_cleans_only_new_staging(collection, monkeypatch):
    def broken(*args):raise OSError('disk full')
    monkeypatch.setattr(s, 'copy_file', broken)
    with pytest.raises(OSError, match='disk full'):s.create_snapshot([collection], 'failed', 1)
    assert not list(s.SNAPSHOTS.iterdir())
    assert (collection / 'success/meta/info.json').is_file()


def test_writer_lock_and_symlink_are_rejected(collection):
    with (collection / '.collection.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='Recorder'):s.create_snapshot([collection], 'busy', 1)
    (collection / 'success/link').symlink_to(collection / 'collection.json')
    with pytest.raises(ValueError, match='regular files'):s.create_snapshot([collection], 'link', 1)


def test_atomic_publish_never_replaces_existing_empty_directory(tmp_path):
    a,b=tmp_path/'staging',tmp_path/'existing';a.mkdir();b.mkdir()
    with pytest.raises(FileExistsError):s.publish(a,b)
    assert a.is_dir() and b.is_dir()


def test_incompatible_inputs_rejected_before_merge(collection):
    first = s.create_snapshot([collection], 'first', 1)
    path=Path(first['snapshot_path'])/'meta/info.json'
    info=json.loads(path.read_text());info['fps']=30;path.write_text(json.dumps(info))
    with pytest.raises(ValueError):s.create_snapshot([collection,path.parent.parent], 'bad', 2)
    assert not (s.SNAPSHOTS/'bad').exists()


def test_official_merge_failure_never_publishes(collection, monkeypatch):
    first=s.create_snapshot([collection], 'first', 1)
    def broken(*args):raise OSError('merge failed')
    monkeypatch.setattr(s,'aggregate',broken)
    with pytest.raises(OSError,match='merge failed'):
        s.create_snapshot([collection,Path(first['snapshot_path'])], 'failed_merge', 2)
    assert not (s.SNAPSHOTS/'failed_merge').exists()
    assert (s.SNAPSHOTS/'first/snapshot_manifest.json').is_file()
    assert not list(s.SNAPSHOTS.glob('.*.copying-*'))
