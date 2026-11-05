"""Declarative HTTP connectors. JSON describes data mappings, never executable code."""
import html
import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

TOKEN = re.compile(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}')
CANONICAL_FIELDS = {'id', 'title', 'excerpt', 'content', 'cover_file', 'cover_id', 'alt', 'category', 'author', 'tags', 'credit', 'slug', 'seo_title', 'seo_description'}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Operation(StrictModel):
    method: Literal['GET', 'POST', 'PUT', 'PATCH'] = 'POST'
    path: str = Field(min_length=1, max_length=2048)
    encoding: Literal['json', 'form', 'multipart'] = 'json'
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, str] = Field(default_factory=dict)
    fileField: str = Field(default='file', pattern=r'^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$')
    payloadField: str | None = Field(default=None, pattern=r'^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$')
    idempotencyHeader: str = Field(default='Idempotency-Key', pattern=r'^[a-zA-Z][a-zA-Z0-9-]{0,63}$')
    urlTemplate: str | None = Field(default=None, max_length=2048)

    @model_validator(mode='after')
    def valid(self):
        if (not self.path.startswith('/') or self.path.startswith('//') or '\\' in self.path
                or any(ord(char) < 32 for char in self.path)):
            raise ValueError('Endpoint paths must stay on the configured website origin.')
        if self.idempotencyHeader.lower() in ('authorization', 'host', 'cookie', 'content-type', 'content-length'):
            raise ValueError('Use a dedicated idempotency header.')
        return self


class Authentication(StrictModel):
    type: Literal['json-login', 'basic', 'bearer'] = 'json-login'
    login: Operation | None = None
    tokenPath: str = Field(default='token', max_length=200)
    header: str = Field(default='Authorization', pattern=r'^[a-zA-Z][a-zA-Z0-9-]{0,63}$')
    prefix: str = Field(default='Bearer ', max_length=50)

    @model_validator(mode='after')
    def valid(self):
        if self.type == 'json-login' and (not self.login or self.login.method != 'POST'):
            raise ValueError('JSON login requires a POST login operation.')
        if self.header.lower() in ('host', 'content-type', 'content-length', 'cookie') or any(ord(char) < 32 for char in self.prefix):
            raise ValueError('Invalid authentication header.')
        return self


class ContentField(StrictModel):
    label: str = Field(default='', max_length=100)
    type: Literal['string', 'number', 'boolean'] = 'string'
    required: bool = False
    maxLength: int | None = Field(default=None, ge=1, le=120000)
    default: str | int | float | bool | None = None


class Operations(StrictModel):
    test: Operation
    publish: Operation
    verify: Operation
    commentLink: Operation
    upload: Operation | None = None
    update: Operation | None = None
    catalog: Operation | None = None


class ConnectorSchema(StrictModel):
    authentication: Authentication
    fields: dict[str, ContentField] = Field(default_factory=dict, max_length=50)
    operations: Operations
    requireAny: list[list[str]] = Field(default_factory=list, max_length=20)
    publicationStatusField: str = Field(default='status', pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$')
    publishedValue: str = Field(default='published', min_length=1, max_length=100)
    verifyValue: str | bool | int = True
    contentFormat: Literal['plain', 'html', 'lexical'] = 'plain'
    catalogArticleMapping: dict[str, str] = Field(default_factory=dict)
    idempotentMutations: Literal[True]

    @model_validator(mode='after')
    def valid(self):
        for key in self.fields:
            if key not in CANONICAL_FIELDS and not re.fullmatch(r'custom_fields\.[a-zA-Z_][a-zA-Z0-9_]{0,63}', key):
                raise ValueError('Content fields must be standard article fields or custom_fields.NAME.')
        for group in self.requireAny:
            if not group or len(group) > 20 or any(key not in self.fields and key not in CANONICAL_FIELDS for key in group):
                raise ValueError('requireAny groups must name known content fields.')
        for name in ('test', 'verify', 'catalog'):
            operation = getattr(self.operations, name)
            if operation and operation.method != 'GET':
                raise ValueError('Connection tests, verification, and catalogs must use GET.')
        for name in ('publish', 'update', 'upload', 'commentLink'):
            operation = getattr(self.operations, name)
            if operation and operation.method == 'GET':
                raise ValueError('Mutation operations cannot use GET.')
        for name, keys in {'publish': ('id', 'status'), 'update': ('id', 'status'), 'verify': ('published',), 'upload': ('id',)}.items():
            operation = getattr(self.operations, name)
            if operation and any(key not in operation.response for key in keys):
                raise ValueError(f'{name} operation is missing required response mappings.')
        for name in ('publish', 'update', 'commentLink'):
            operation = getattr(self.operations, name)
            if operation and 'url' not in operation.response and not operation.urlTemplate:
                raise ValueError(f'{name} operation must map a public URL.')
        if self.operations.upload and self.operations.upload.encoding != 'multipart':
            raise ValueError('Separate file uploads require multipart encoding.')
        for name, operation in self.operations:
            if operation and self.authentication.type != 'basic' and operation.idempotencyHeader.lower() == self.authentication.header.lower():
                raise ValueError('Authentication and idempotency must use different headers.')
            if operation and re.search(r'\{\{\s*credentials\.', operation.model_dump_json()):
                raise ValueError('Credential references are allowed only in authentication.login.')
        return self


def get_path(value, path):
    if path in ('', '$'):
        return value
    for part in path.split('.'):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError('A configured website field or response mapping was not found.')
    return value


def render(value, context, *, path=False):
    if isinstance(value, dict):
        return {key: render(item, context, path=path) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, context, path=path) for item in value]
    if not isinstance(value, str):
        return value
    match = TOKEN.fullmatch(value)
    if match and not path:
        return get_path(context, match.group(1))
    def replace(match):
        item = get_path(context, match.group(1))
        if isinstance(item, (dict, list)):
            raise ValueError('Structured values need an entire template value.')
        return quote(str(item), safe='') if path else str(item)
    output = TOKEN.sub(replace, value)
    if '{{' in output or '}}' in output:
        raise ValueError('Invalid website template syntax.')
    return output


def schema_for(site):
    value = site.value.get('schema')
    return ConnectorSchema.model_validate(value) if value else None


def safe_url(base, value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(char) < 32 for char in value):
        raise ValueError('Website did not return a valid public URL.')
    url = urljoin(base + '/', value)
    expected, actual = urlsplit(base), urlsplit(url)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc) or actual.username or actual.password:
        raise ValueError('Website returned a URL outside its configured origin.')
    return url


def operate(site, operation, context, *, file=None, published=None):
    context = {'baseUrl': site.value['baseUrl'], **context}
    path = render(operation.path, context, path=True)
    # Validate again after substitution, before attaching credentials.
    if path.startswith('//') or '\\' in path or not path.startswith('/'):
        raise ValueError('Endpoint paths must stay on the configured website origin.')
    body = render(operation.body, context)
    if published:
        field, status = published
        target = body
        keys = field.split('.')
        for key in keys[:-1]:
            target = target.setdefault(key, {})
            if not isinstance(target, dict):
                raise ValueError('Published status field must point into an object.')
        target[keys[-1]] = status
    kwargs = {'params': render(operation.query, context)}
    if context.get('request_id') and operation.method != 'GET':
        kwargs['headers'] = {operation.idempotencyHeader: context['request_id']}
    if operation.method != 'GET':
        if operation.encoding == 'json':
            if file:
                raise ValueError('Configure a multipart upload operation for cover images.')
            kwargs['json'] = body
        else:
            data = {operation.payloadField: json.dumps(body)} if operation.payloadField else {
                key: json.dumps(item) if isinstance(item, (dict, list, bool)) else item for key, item in body.items()}
            if file:
                if operation.encoding != 'multipart':
                    raise ValueError('Configure multipart encoding for file uploads.')
                kwargs['data'] = data
                kwargs['files'] = {operation.fileField: file}
            elif operation.encoding == 'multipart':
                # Preserve multipart encoding when using an existing cover ID.
                kwargs['files'] = {key: (None, '' if item is None else str(item)) for key, item in data.items()}
            else:
                kwargs['data'] = data
    response = site.request(operation.method, path, **kwargs)
    if not isinstance(response, dict):
        raise ValueError('Website API response must be a JSON object.')
    mapped = {key: get_path(response, source) for key, source in operation.response.items()}
    if operation.urlTemplate:
        mapped['url'] = render(operation.urlTemplate, {**context, 'response': response})
    return mapped if operation.response or operation.urlTemplate else response


def authenticate(site):
    config = schema_for(site)
    if not config:
        response = site.request('POST', '/api/users/login', json={'email': site.value['email'], 'password': site.value['password']})
        token = response.get('token')
        if not isinstance(token, str) or not token:
            raise ValueError('Website login did not return a token.')
        site.client.headers['Authorization'] = 'JWT ' + token
        return
    authentication = config.authentication
    if authentication.type == 'basic':
        site.client.auth = httpx.BasicAuth(site.value.get('email', ''), site.value.get('password', ''))
        return
    if authentication.type == 'bearer':
        token = site.value.get('api_token', '')
    else:
        response = operate(site, authentication.login, {'credentials': {
            'email': site.value.get('email', ''), 'username': site.value.get('email', ''),
            'password': site.value.get('password', '')}})
        token = get_path(response, authentication.tokenPath)
    if not isinstance(token, str) or not token or any(ord(char) < 32 for char in token):
        raise ValueError('Website authentication did not provide a valid token.')
    site.client.headers[authentication.header] = authentication.prefix + token


def check(site):
    config = schema_for(site)
    if config:
        operate(site, config.operations.test, {})
    elif site.request('GET', '/api/studio').get('adapter') != 'payload-news-v1':
        raise ValueError('This website does not support the Payload news Studio adapter.')


def catalog(site):
    config = schema_for(site)
    if not config:
        return site.request('GET', '/api/studio')
    result = operate(site, config.operations.catalog, {}) if config.operations.catalog else {}
    articles = result.get('articles', [])
    if config.catalogArticleMapping:
        articles = [{key: get_path(article, source) for key, source in config.catalogArticleMapping.items()} for article in articles]
    normalized = []
    for article in articles:
        row = {key: str(article.get(key) or '') for key in CANONICAL_FIELDS}
        row.update(status=str(article.get('status', '')), custom_fields=article.get('custom_fields', {}))
        normalized.append(row)
    return {'articles': normalized, 'categories': result.get('categories', []), 'authors': result.get('authors', [])}


def article_values(article, config):
    value = article.model_dump()
    for key, field in config.fields.items():
        if key.startswith('custom_fields.'):
            name = key.split('.', 1)[1]
            item = value['custom_fields'].get(name, field.default)
            value['custom_fields'][name] = item
        else:
            item = value.get(key)
            if item in ('', None) and field.default is not None:
                item = value[key] = field.default
        if field.required and (item is None or item == ''):
            raise ValueError(f'Website requires {field.label or key}.')
        if item is None or item == '':
            continue
        if field.type == 'string' and (not isinstance(item, str) or (field.maxLength and len(item) > field.maxLength)):
            raise ValueError(f'Invalid value or length for website field {field.label or key}.')
        if field.type == 'number' and (not isinstance(item, (int, float)) or isinstance(item, bool)):
            raise ValueError(f'Website field {field.label or key} must be a number.')
        if field.type == 'boolean' and not isinstance(item, bool):
            raise ValueError(f'Website field {field.label or key} must be a boolean.')
    for group in config.requireAny:
        if not any(get_path(value, key) not in ('', None) for key in group):
            raise ValueError('Website requires one of: ' + ', '.join(group))
    if config.contentFormat == 'html':
        value['content'] = ''.join('<p>' + html.escape(paragraph).replace('\n', '<br>') + '</p>' for paragraph in re.split(r'\n\s*\n', value['content']) if paragraph.strip())
    if config.contentFormat == 'lexical':
        value['content'] = {'root': {'type': 'root', 'version': 1, 'format': '', 'indent': 0, 'direction': None,
            'children': [{'type': 'paragraph', 'version': 1, 'format': '', 'indent': 0, 'direction': None,
                'children': [{'type': 'text', 'version': 1, 'text': paragraph, 'format': 0, 'detail': 0, 'mode': 'normal', 'style': ''}]} for paragraph in re.split(r'\n\s*\n', value['content']) if paragraph.strip()]}}
    value['status'] = config.publishedValue
    return value


def publish(site, article, request_id, file=None):
    config = schema_for(site)
    if not config:
        result = site.request('POST', '/api/studio', data={'payload': json.dumps({'action': 'publish', 'requestId': request_id,
            'article': article.model_dump(exclude={'custom_fields'})})}, files={'file': file} if file else None)
    else:
        values = article_values(article, config)
        context = {'article': values, 'request_id': request_id, 'published_status': config.publishedValue}
        if file and config.operations.upload:
            media = operate(site, config.operations.upload, {**context, 'request_id': hashlib.sha256((request_id + ':cover').encode()).hexdigest()}, file=file)
            if 'id' not in media:
                raise ValueError('Website upload response must map an id.')
            values['cover_id'] = media['id']
            file = None
        operation = config.operations.update if article.id else config.operations.publish
        if not operation:
            raise ValueError('This website schema does not support updating existing articles.')
        result = operate(site, operation, context, file=file, published=(config.publicationStatusField, config.publishedValue))
        if result.get('status') != config.publishedValue:
            raise ValueError('Website has not confirmed a published article. Facebook work is blocked.')
        result['status'] = 'published'
    if result.get('status') != 'published' or not result.get('id'):
        raise ValueError('Website has not confirmed a published article. Facebook work is blocked.')
    result['id'] = str(result['id'])
    result['url'] = safe_url(site.value['baseUrl'], result.get('url'))
    return result


def verify(site, article):
    config = schema_for(site)
    if config:
        result = operate(site, config.operations.verify, {'article': article})
        # Strict comparison: string "false" must never confirm publication.
        return type(result.get('published')) is type(config.verifyValue) and result.get('published') == config.verifyValue
    return site.request('GET', '/api/studio', params={'published': article['id']}).get('published') is True


def comment_link(site, article, request_id, label):
    config = schema_for(site)
    result = operate(site, config.operations.commentLink, {'article': article, 'request_id': request_id, 'label': label}) if config else site.request('POST', '/api/studio', json={
        'action': 'link', 'requestId': request_id, 'articleId': article['id'], 'label': label})
    result['url'] = safe_url(site.value['baseUrl'], result.get('url'))
    return result
