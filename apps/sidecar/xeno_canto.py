"""XC API v3 search and explicitly selected, attributed local audio downloads."""
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from storage import CatalogError, checked_source, connect


class XCRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlparse(newurl)
        if url.scheme != 'https' or url.hostname not in {'xeno-canto.org', 'www.xeno-canto.org'} or url.port not in (None, 443) or url.username:
            raise CatalogError('xc_redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def key_status(root):
    return {'configured': (Path(root) / '.xeno-canto-key').is_file()}


def save_key(root, key):
    path = Path(root) / '.xeno-canto-key'
    if not key:
        path.unlink(missing_ok=True)
    else:
        # The session API never returns the key; mkstemp restricts access on POSIX.
        fd, name = tempfile.mkstemp(dir=root, prefix='.xc-key-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(key)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    return key_status(root)


def download(url, output, maximum):
    request = Request(url, headers={'User-Agent': 'Echoroo-Lite/0.1 (https://echoroo.app)'})
    try:
        with build_opener(XCRedirect()).open(request, timeout=25) as response:
            if int(response.headers.get('Content-Length', '0')) > maximum:
                raise CatalogError('xc_too_large')
            size, deadline = 0, time.monotonic() + 60
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise CatalogError('xc_too_large')
                if time.monotonic() > deadline:
                    raise CatalogError('xc_unavailable')
                output.write(chunk)
    except HTTPError as error:
        code = 'xc_access_denied' if error.code in (401, 403) else 'xc_rate_limit' if error.code == 429 else 'xc_unavailable'
        raise CatalogError(code) from None
    except (OSError, URLError, ValueError):
        # Never include the upstream URL: the API key is a query parameter.
        raise CatalogError('xc_unavailable') from None


def search_page(key_path, scientific, country, quality, page):
    from io import BytesIO
    if any('"' in value or any(ord(c) < 32 for c in value) for value in (scientific, country)):
        raise CatalogError('invalid_input')
    try:
        key = Path(key_path).read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        raise CatalogError('xc_key_required') from None
    if not key:
        raise CatalogError('xc_key_required')
    query = f'sp:"{scientific}"'
    if country:
        query += f' cnt:"{country}"'
    if quality:
        query += f' q:{quality}'
    buffer = BytesIO()
    download('https://xeno-canto.org/api/3/recordings?' + urlencode({'query': query, 'page': page, 'key': key}), buffer, 10 * 1024 * 1024)
    try:
        data = json.loads(buffer.getvalue())
        recordings = []
        for raw in data['recordings']:
            if not raw.get('file'):
                continue  # Restricted recordings have no downloadable file; offering them only fails later.
            rid = int(raw['id'])
            if rid <= 0:
                raise ValueError()
            recordings.append({'id': rid, 'scientific_name': f"{raw['gen']} {raw['sp']}",
                'common_name': raw.get('en', ''), 'recordist': raw.get('rec', ''),
                'country': raw.get('cnt', ''), 'location': raw.get('loc', ''),
                'quality': raw.get('q', ''), 'length': raw.get('length', ''),
                'type': raw.get('type', ''), 'date': raw.get('date', ''),
                'license': raw.get('lic', ''), 'url': f'https://xeno-canto.org/{rid}'})
        result = {'recordings': recordings, 'page': int(data.get('page', page)),
                  'total_pages': max(1, int(data['numPages'])), 'total_recordings': int(data['numRecordings'])}
    except (KeyError, TypeError, ValueError):
        raise CatalogError('xc_invalid_response') from None
    return result


def search(root, scientific, country, quality, page):
    result = search_page(Path(root) / '.xeno-canto-key', scientific, country, quality, page)
    with connect(root) as db:
        # Downloaded attribution is immutable even if a later API response changes.
        db.executemany('''INSERT INTO xc_recordings(id,metadata) VALUES(?,?) ON CONFLICT(id)
            DO UPDATE SET metadata=excluded.metadata WHERE xc_recordings.reference_id IS NULL''',
                       ((r['id'], json.dumps(r, ensure_ascii=False)) for r in result['recordings']))
    return result


def import_recording(root, xc_id):
    import soundfile as sf
    from audio import add_reference, source
    with connect(root) as db:
        record = db.execute('SELECT * FROM xc_recordings WHERE id=?', (xc_id,)).fetchone()
    if record is None:
        raise CatalogError('not_found')
    if record['reference_id']:
        row = source(root, 'reference', record['reference_id'])
        checked_source(row)
        return row
    folder = Path(root) / 'references' / 'xeno-canto'
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=folder, suffix='.audio')
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as output:
            download(f'https://xeno-canto.org/{xc_id}/download', output, 50 * 1024 * 1024)
        try:
            info = sf.info(temporary)
            extension = {'MP3': '.mp3', 'WAV': '.wav', 'FLAC': '.flac'}[info.format]
        except (RuntimeError, KeyError):
            raise CatalogError('reference_format') from None
        if info.frames <= 0 or info.channels not in (1, 2):
            raise CatalogError('reference_channels')
        path = folder / f'XC{xc_id}{extension}'
        os.replace(temporary, path)
        row = add_reference(root, path)
        with connect(root) as db:
            db.execute('UPDATE xc_recordings SET reference_id=? WHERE id=?', (row['id'], xc_id))
        return source(root, 'reference', row['id'])
    finally:
        temporary.unlink(missing_ok=True)
