"""
Standalone API Validation Endpoint for testing LLM and E2B configurations.
Does not require database connection.
"""
import logging
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx

from app.core.llm_security import (
    normalize_llm_model,
    safe_llm_base_url as _safe_llm_base_url,
)

from app.infra.gateways.anthropic_compat import (
    anthropic_headers,
    anthropic_messages_url,
    anthropic_payload,
    is_anthropic_compatible_base,
)

logger = logging.getLogger(__name__)

router = APIRouter()

def _redact(value: str, secret: str = "") -> str:
    text = str(value)
    return text.replace(secret, "[REDACTED]")[:300] if secret else text[:300]


class ValidateConfigRequest(BaseModel):
    """Request body for validation."""
    provider: str  # "llm" or "e2b"
    apiKey: Optional[str] = None
    baseUrl: Optional[str] = None
    modelName: Optional[str] = None


class ValidateConfigResponse(BaseModel):
    """Response for validation."""
    success: bool
    message: str
    details: Optional[dict] = None


@router.post("/validate", response_model=ValidateConfigResponse)
async def validate_config(request: ValidateConfigRequest):
    """
    Validate API configuration by making a test request.
    
    - **llm**: Test LLM API key by calling the model list endpoint
    - **e2b**: Test E2B API key by listing available sandboxes
    """
    # Check if API key is provided
    if not request.apiKey:
        return ValidateConfigResponse(
            success=False,
            message="Please enter your API key in the settings first.",
            details={"error": "No API key provided"}
        )
    
    if request.provider == "llm":
        return await validate_llm_config(
            api_key=request.apiKey,
            base_url=request.baseUrl,
            model_name=request.modelName
        )
    
    elif request.provider == "e2b":
        return await validate_e2b_config(api_key=request.apiKey)
    
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {request.provider}. Must be 'llm' or 'e2b'"
        )


async def validate_llm_config(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None
) -> ValidateConfigResponse:
    """
    Validate LLM API configuration.
    Makes a REAL chat completion call to verify credentials AND model work.
    """
    try:
        # Test with actual chat completion call
        test_api_key = api_key or ""
        test_base_url = _safe_llm_base_url(base_url, model_name)
        test_model = normalize_llm_model(model_name or "gpt-5.6-sol")

        async with httpx.AsyncClient(timeout=30.0) as client:
            if is_anthropic_compatible_base(test_base_url):
                response = await client.post(
                    anthropic_messages_url(test_base_url),
                    headers=anthropic_headers(test_api_key, test_base_url),
                    json=anthropic_payload(
                        [{"role": "user", "content": "Hi"}],
                        test_model,
                        max_tokens=16,
                    ),
                    follow_redirects=False
                )
                if response.status_code == 200:
                    return ValidateConfigResponse(
                        success=True,
                        message=f"LLM API test successful! Model '{test_model}' is working.",
                        details={"status_code": response.status_code, "model": test_model}
                    )
                if response.status_code == 401:
                    return ValidateConfigResponse(
                        success=False,
                        message="Invalid API key. Please check your credentials.",
                        details={"status_code": 401, "error": "Unauthorized"}
                    )
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", response.text)
                except Exception:
                    error_msg = response.text[:200]
                error_msg = _redact(str(error_msg), test_api_key)[:300]
                return ValidateConfigResponse(
                    success=False,
                    message=f"API error ({response.status_code}): {error_msg}",
                    details={"status_code": response.status_code, "response": error_msg}
                )

            # Use chat/completions endpoint (not /models)
            test_url = f"{test_base_url.rstrip('/')}/chat/completions"

            headers = {}
            if "anthropic.com" in test_base_url:
                # Anthropic API
                headers = {
                    "x-api-key": test_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }
            else:
                # OpenAI-compatible APIs (OpenRouter, SiliconFlow, Zhipu, etc.)
                headers = {
                    "Authorization": f"Bearer {test_api_key}",
                    "Content-Type": "application/json"
                }

            try:
                # Make a REAL chat completion call with minimal test message
                payload = {
                    "model": test_model,
                    "messages": [{"role": "user", "content": "Hi"}],
                }
                if test_model.lower().startswith("gpt-5"):
                    payload["max_completion_tokens"] = 16
                    payload["reasoning_effort"] = os.getenv("REASONING_EFFORT", "low")
                else:
                    payload["max_tokens"] = 16
                if test_model.lower().startswith("deepseek-v4"):
                    payload["thinking"] = {"type": "enabled"}
                    payload["reasoning_effort"] = "low"
                response = await client.post(
                    test_url,
                    headers=headers,
                    json=payload,
                    follow_redirects=False
                )

                if response.status_code == 200:
                    return ValidateConfigResponse(
                        success=True,
                        message=f"LLM API test successful! Model '{test_model}' is working.",
                        details={"status_code": response.status_code, "model": test_model}
                    )
                elif response.status_code == 401:
                    return ValidateConfigResponse(
                        success=False,
                        message="Invalid API key. Please check your credentials.",
                        details={"status_code": 401, "error": "Unauthorized"}
                    )
                elif response.status_code == 403:
                    return ValidateConfigResponse(
                        success=False,
                        message="Access denied. Please check your API permissions.",
                        details={"status_code": 403, "error": "Forbidden"}
                    )
                else:
                    # Try to parse error message from response
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", {}).get("message", str(response.text))
                    except:
                        error_msg = response.text[:200]
                    error_msg = _redact(str(error_msg), test_api_key)[:300]

                    return ValidateConfigResponse(
                        success=False,
                        message=f"API error ({response.status_code}): {error_msg}",
                        details={"status_code": response.status_code, "response": error_msg}
                    )
                    
            except httpx.ConnectError as e:
                return ValidateConfigResponse(
                    success=False,
                    message="Cannot connect to API server. Check your base URL.",
                    details={"error": _redact(f"Connection failed: {str(e)}", test_api_key)}
                )
            except httpx.TimeoutException:
                return ValidateConfigResponse(
                    success=False,
                    message="API request timed out. Server may be slow or unreachable.",
                    details={"error": "Timeout after 30s"}
                )
                
    except Exception as e:
        logger.error("LLM validation error: %s", _redact(str(e), test_api_key if "test_api_key" in locals() else ""))
        return ValidateConfigResponse(
            success=False,
            message=f"Validation failed: {_redact(str(e), test_api_key if 'test_api_key' in locals() else '')}",
            details={"error": _redact(str(e), test_api_key if "test_api_key" in locals() else "")}
        )


async def validate_e2b_config(api_key: str) -> ValidateConfigResponse:
    """
    Validate E2B API configuration.
    Lists available sandboxes to verify credentials.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # E2B API uses X-API-Key header, not Authorization Bearer
                response = await client.get(
                    "https://api.e2b.dev/v2/sandboxes",
                    headers={
                        "X-API-Key": api_key,
                        "Content-Type": "application/json"
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # Handle both list and dict responses
                    if isinstance(data, list):
                        sandboxes = data
                    else:
                        sandboxes = data.get("data", data.get("sandboxes", []))
                    return ValidateConfigResponse(
                        success=True,
                        message=f"E2B API connection successful! ({len(sandboxes)} sandboxes available)",
                        details={"sandboxes_count": len(sandboxes)}
                    )
                elif response.status_code == 401:
                    return ValidateConfigResponse(
                        success=False,
                        message="Invalid E2B API key. Please check your credentials.",
                        details={"status_code": 401, "error": "Unauthorized"}
                    )
                elif response.status_code == 403:
                    error_msg = "Access denied. Your E2B team account may be blocked or inactive."
                    return ValidateConfigResponse(
                        success=False,
                        message=error_msg,
                        details={"status_code": 403, "error": "Forbidden - team blocked or inactive"}
                    )
                else:
                    return ValidateConfigResponse(
                        success=False,
                        message=f"E2B API returned error: {response.status_code}",
                        details={"status_code": response.status_code, "response": response.text[:200]}
                    )
                    
            except httpx.ConnectError as e:
                return ValidateConfigResponse(
                    success=False,
                    message="Cannot connect to E2B server. Check your internet connection.",
                    details={"error": f"Connection failed: {str(e)}"}
                )
                
    except Exception as e:
        logger.error(f"E2B validation error: {e}")
        return ValidateConfigResponse(
            success=False,
            message=f"E2B validation failed: {str(e)}",
            details={"error": str(e)}
        )
