import responses
import pytest
import json
import gzip
import sys
import requests
from time import sleep

from six import StringIO

from libtaxii import messages_11 as tm11
from libtaxii import messages_10 as tm10
from libtaxii.constants import (
    VID_TAXII_XML_11, VID_TAXII_XML_10,
)

from cabby import create_client
from cabby import exceptions as exc

import fixtures11
import fixtures10

CUSTOM_HEADER_NAME = 'X-custom-header'
CUSTOM_HEADER_VALUE = 'header value with space!'


def get_fix(version):
    return (fixtures10 if version == 10 else fixtures11)


def make_client(version, **kwargs):
    client = create_client(
        get_fix(version).HOST,
        version=("1.1" if version == 11 else "1.0"),
        **kwargs)
    return client


def register_uri(uri, body, version, headers=None, mock=None, **kwargs):
    content_type = VID_TAXII_XML_11 if version == 11 else VID_TAXII_XML_10
    headers = headers or {}
    headers.update({
        'X-TAXII-Content-Type': content_type
    })
    if not mock:
        mock = responses
    mock.add(
        method=responses.POST,
        url=uri,
        body=body,
        content_type='application/xml',
        stream=True,
        adding_headers=headers,
        **kwargs)


def get_sent_message(version, mock=None):
    if not mock:
        mock = responses
    body = mock.calls[-1].request.body
    print(repr(body))
    return (tm11 if version == 11 else tm10).get_message_from_xml(body)


# Tests


@pytest.mark.parametrize("version", [11, 10])
@responses.activate
def test_set_headers(version):
    uri = get_fix(version).DISCOVERY_URI_HTTP
    response = get_fix(version).DISCOVERY_RESPONSE

    register_uri(uri, response, version)

    client = make_client(
        version,
        headers={CUSTOM_HEADER_NAME: CUSTOM_HEADER_VALUE})

    services = client.discover_services(uri=uri)

    assert len(services) == 4

    message = get_sent_message(version)
    expected_type = (tm11 if version == 11 else tm10).DiscoveryRequest
    assert type(message) == expected_type

    last_request = responses.calls[-1].request

    assert CUSTOM_HEADER_NAME in last_request.headers
    assert last_request.headers[CUSTOM_HEADER_NAME] == CUSTOM_HEADER_VALUE


@pytest.mark.parametrize("version", [11, 10])
@responses.activate
def test_invalid_response(version):
    uri = get_fix(version).DISCOVERY_URI_HTTP

    # FIXME: httpretty returns body as byte string (utf-8 encoded)
    # and when libtaxii tries to join headers (unicode) with the body (binary)
    # error happens. Line in Libtaxii codebase
    # https://github.com/EclecticIQ/libtaxii/blob/master/libtaxii/__init__.py#L126
    return

    responses.add(
        method=responses.POST,
        url=uri,
        body='INVALID-BODY',
        content_type='text/html',
    )

    client = make_client(version)

    with pytest.raises(exc.InvalidResponseError):
        client.discover_services(uri=uri)


@pytest.mark.parametrize("version", [11, 10])
@responses.activate
def test_invalid_response_status(version):
    uri = get_fix(version).DISCOVERY_URI_HTTP

    responses.add(method=responses.POST, url=uri, status=404)

    client = make_client(version)

    with pytest.raises(exc.HTTPError):
        client.discover_services(uri=uri)


@pytest.mark.parametrize("version", [11, 10])
@responses.activate
def test_jwt_auth_response(version):
    jwt_path = '/management/auth/'
    jwt_url = 'http://{}{}'.format(get_fix(version).HOST, jwt_path)

    token = 'dummy'
    username = 'dummy-username'
    password = 'dummy-password'

    def jwt_request_callback(request):
        body = request.body
        if isinstance(body, bytes):
            body = body.decode()
        body = json.loads(body)

        assert body['username'] == username
        assert body['password'] == password

        content = json.dumps({'token': token}).encode()
        return (200, {}, content)

    # https://github.com/getsentry/responses/pull/268
    responses.mock._matches.append(responses.CallbackResponse(
        method=responses.POST,
        url=jwt_url,
        callback=jwt_request_callback,
        content_type='application/json',
        stream=True,
    ))
    discovery_uri = get_fix(version).DISCOVERY_URI_HTTP

    register_uri(
        discovery_uri,
        get_fix(version).DISCOVERY_RESPONSE,
        version)

    print(version, get_fix(version).DISCOVERY_RESPONSE)

    # client with relative JWT auth path
    client = make_client(version)
    client.set_auth(
        username=username,
        password=password,
        jwt_auth_url=jwt_path
    )
    services = client.discover_services(uri=discovery_uri)
    assert len(services) == 4

    # client with full JWT auth path
    client = make_client(version)
    client.set_auth(
        username=username,
        password=password,
        jwt_auth_url=jwt_url
    )
    services = client.discover_services(uri=discovery_uri)
    assert len(services) == 4


def compress(text):
    if sys.version_info < (3, 2):
        out = StringIO()
        with gzip.GzipFile(fileobj=out, mode="w") as f:
            f.write(text)
        return out.getvalue()
    else:
        return gzip.compress(text.encode('utf-8'))


@pytest.mark.parametrize("version", [11, 10])
@responses.activate
def test_gzip_response(version):
    uri = get_fix(version).DISCOVERY_URI_HTTP
    response = get_fix(version).DISCOVERY_RESPONSE

    body = compress(response)

    register_uri(uri, body, version, headers={
        'Content-Encoding': 'application/gzip'
    })

    client = make_client(version)

    services = client.discover_services(uri=uri)
    assert len(services) == 4


@pytest.mark.parametrize("version", [11, 10])
@responses.activate
def test_timeout(version):
    # this can't be tested with responses,
    # because it substitutes requests session
    # and doesn't worry about timeouts
    pytest.skip("cannot be tested yet")

    timeout_in_sec = 1
    client = make_client(version)
    # configure to raise the error before the timeout
    client.timeout = timeout_in_sec / 2.0

    def timeout_request_callback(request):
        sleep(timeout_in_sec)
        return (200, {'X-TAXII-Content-Type': content_type}, "All good!")

    uri = get_fix(version).DISCOVERY_URI_HTTP

    # https://github.com/getsentry/responses/pull/268
    content_type = VID_TAXII_XML_11 if version == 11 else VID_TAXII_XML_10
    responses.mock._matches.append(responses.CallbackResponse(
        responses.POST,
        uri,
        callback=timeout_request_callback,
        content_type='application/json',
        stream=True,
    ))

    with pytest.raises(requests.exceptions.Timeout):
        client.discover_services(uri=uri)
