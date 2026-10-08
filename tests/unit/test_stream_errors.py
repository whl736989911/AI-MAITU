from octop.i18n.domains.stream import (
    MODEL_RETRY_FAILURE_MARK,
    classify_stream_error_message,
    format_stream_error,
)


def test_retry_failure_error_formatting_keeps_known_cause() -> None:
    message = format_stream_error(
        RuntimeError("Model call failed after 3 attempts with HTTP 429: rate_limit_exceeded"),
        "en",
    )

    assert "rate-limited" in message
    assert (
        classify_stream_error_message(
            "Model call failed after 3 attempts with HTTP 429: rate_limit_exceeded"
        )
        == "octop:stream_errors.rate_limit"
    )


def test_retry_marker_preserves_json_braces_in_provider_cause() -> None:
    cause = "HTTP 400: {'error': {'message': 'bad request'}}"
    message = format_stream_error(
        f"{MODEL_RETRY_FAILURE_MARK}\nThe model API request failed.\n\nTechnical detail: {cause}",
        "en",
    )

    assert cause in message
    assert "{{'error': {{'message': 'bad request'}}}}" not in message
