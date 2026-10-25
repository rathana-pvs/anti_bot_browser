"""Extract public article HTML without browser sessions or internal-network access."""
import ipaddress
import socket
import time
import zlib
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import trafilatura

MAX_HTML = 3 * 1024 * 1024
MAX_TEXT = 120_000


def public_url(value):
    try:
        parts = urlsplit(value.strip())
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443 if parts.scheme == 'https' else 80)
                or any(c in parts.hostname for c in ('%', '\\', ' ', '\n', '\r'))):
            raise ValueError()
        url = httpx.URL(urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, '')))
        return url
    except (ValueError, httpx.InvalidURL):
        raise ValueError('Use a public HTTP/HTTPS article URL without login details or a custom port.') from None


def public_addresses(host, port):
    try:
        entries = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError('The article website could not be found. Check the URL or paste its text.') from None
    addresses = list(dict.fromkeys(entry[4][0] for entry in entries))
    if not addresses:
        raise ValueError('The article website has no reachable address.')
    for value in addresses:
        ip = ipaddress.ip_address(value)
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError('Only public article websites are supported. Local/private network URLs are blocked.')
    return addresses


def download_article(url, client_factory=httpx.Client, resolver=public_addresses):
    url = public_url(url)
    started = time.monotonic()
    # Connections use the validated IP itself. Host and TLS SNI retain the
    # article hostname, so a second DNS lookup cannot rebind to an internal IP.
    try:
        with client_factory(timeout=httpx.Timeout(12, connect=8), trust_env=False, follow_redirects=False) as client:
            for _ in range(6):
                address = resolver(url.host, url.port or (443 if url.scheme == 'https' else 80))[0]
                target = url.copy_with(host=address)
                headers = {'Host': url.host, 'Accept': 'text/html,application/xhtml+xml',
                           'Accept-Encoding': 'gzip, deflate', 'User-Agent': 'ReelCreator/1.0 (article text reader)'}
                with client.stream('GET', target, headers=headers, extensions={'sni_hostname': url.host}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get('location')
                        if not location:
                            raise ValueError('The article redirected without a destination. Paste its text instead.')
                        url = public_url(urljoin(str(url), location))
                        if time.monotonic() - started > 35:
                            raise ValueError('The article website took too long. Paste the article text instead.')
                        continue
                    if response.status_code != 200:
                        raise ValueError('The website blocked or could not provide the article. Paste the full text instead.')
                    content_type = response.headers.get('content-type', '').split(';')[0].lower()
                    if content_type not in ('text/html', 'application/xhtml+xml'):
                        raise ValueError('This URL does not return an HTML article. Paste the text instead.')
                    encoding = response.headers.get('content-encoding', 'identity').lower()
                    if encoding not in ('identity', 'gzip', 'deflate'):
                        raise ValueError('This website returned an unsupported compressed response. Paste its text instead.')
                    decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15) if encoding != 'identity' else None
                    data = bytearray()
                    received = 0
                    for chunk in response.iter_raw(64 * 1024):
                        received += len(chunk)
                        data.extend(decoder.decompress(chunk, MAX_HTML - len(data) + 1) if decoder else chunk)
                        if len(data) > MAX_HTML or received > MAX_HTML or time.monotonic() - started > 35:
                            raise ValueError('The article page is too large or slow. Paste the article text instead.')
                    if decoder and (not decoder.eof or decoder.unused_data):
                        raise ValueError('The compressed article page was incomplete. Paste its text instead.')
                    return bytes(data), str(url)
        raise ValueError('The article redirected too many times. Paste its text instead.')
    except (httpx.HTTPError, zlib.error):
        raise ValueError('The article could not be downloaded securely. Check the URL or paste its text.') from None


def extract_article(url, downloader=download_article):
    html, final_url = downloader(url)
    try:
        document = trafilatura.bare_extraction(html, url=final_url, include_comments=False,
                                              include_tables=False, include_links=False,
                                              favor_precision=True, deduplicate=True, with_metadata=True)
        document = document.as_dict() if document else None
    except Exception:
        raise ValueError('The article text could not be extracted. Paste the full text instead.') from None
    text = ((document or {}).get('text') or '').strip()
    if len(text) < 100:
        raise ValueError('Not enough article text was available. The page may require JavaScript or a subscription. Paste the full text instead.')
    if len(text) > MAX_TEXT:
        raise ValueError('This article is too long. Paste the relevant article text (up to 120,000 characters).')
    return dict(url=final_url, title=str(document.get('title') or '')[:300], text=text,
                publisher=str(document.get('sitename') or urlsplit(final_url).hostname or '')[:100],
                published_date=str(document.get('date') or '')[:40], characters=len(text))
