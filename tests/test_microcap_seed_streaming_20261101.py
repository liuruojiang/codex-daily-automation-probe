"""Bounded verified streaming, with no release writes or mail sending."""
import hashlib
import io
import pytest
import test_restore_approved_microcap_state as fixtures

seed = fixtures.seed
URL = fixtures.URL

class BoundedResponse(io.BytesIO):
    def read(self, size=-1):
        assert size == seed.DOWNLOAD_CHUNK_BYTES
        return super().read(size)


def test_streams_multiple_chunks_to_verified_file(tmp_path, monkeypatch):
    payload = b'x' * (seed.DOWNLOAD_CHUNK_BYTES * 2 + 17)
    monkeypatch.setattr(seed.urllib.request, 'urlopen', lambda *a, **k: BoundedResponse(payload))
    target = tmp_path / 'bundle.zip'
    target.write_bytes(b'prior valid state')
    assert seed.download_seed(URL, hashlib.sha256(payload).hexdigest(), target) == target
    assert target.read_bytes() == payload
    assert not list(tmp_path.glob('*.partial'))


@pytest.mark.parametrize('failure', ['hash', 'oversize', 'empty', 'interrupted'])
def test_failed_download_preserves_old_bundle(tmp_path, monkeypatch, failure):
    target = tmp_path / 'bundle.zip'
    target.write_bytes(b'prior valid state')
    payload = b'new candidate'
    expected = hashlib.sha256(payload).hexdigest()
    if failure == 'hash': expected = 'a' * 64
    if failure == 'oversize': monkeypatch.setattr(seed, 'MAX_BUNDLE_BYTES', len(payload)-1)
    if failure == 'empty': payload = b''
    class Interrupted(BoundedResponse):
        calls = 0
        def read(self, size=-1):
            self.calls += 1
            if self.calls == 2: raise OSError('interrupted body')
            return super().read(size)
    response = Interrupted(payload) if failure == 'interrupted' else BoundedResponse(payload)
    monkeypatch.setattr(seed.urllib.request, 'urlopen', lambda *a, **k: response)
    with pytest.raises((OSError, ValueError)):
        seed.download_seed(URL, expected, target)
    assert target.read_bytes() == b'prior valid state'
    assert not list(tmp_path.glob('*.partial'))
