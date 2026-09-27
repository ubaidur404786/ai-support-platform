# API rate limits

The API allows 600 requests per minute per API key. Requests over the limit receive HTTP status 429 Too Many Requests, with a Retry-After header saying how many seconds to wait.

Well-behaved integrations wait for the Retry-After period instead of retrying immediately. Bulk operations should use the batch endpoints, which count as one request for up to 100 items.

Enterprise plans can request a higher limit from support.
