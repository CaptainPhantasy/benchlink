"""Do not forward credential-bearing requests to a Location supplied by a peer."""
import urllib.request


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib raises HTTPError for every 30x; caller preserves error policy.


def open_no_redirects(request, *, timeout):
    # A private opener avoids changing process-global urllib behavior.
    return urllib.request.build_opener(_RejectRedirects()).open(request, timeout=timeout)
