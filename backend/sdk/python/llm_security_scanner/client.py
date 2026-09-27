"""LLM Security Scanner Client."""

import json
import time
from typing import Optional, Dict, Any, List, Union
import requests
import httpx
from .models import (
    ScanResult,
    ChatCompletionResponse,
    ChatCompletionMessage,
    ChatCompletionChoice,
    ChatCompletionUsage,
    RuleMatch,
    ScannerError,
    AuthenticationError,
    RateLimitError,
    ScannerAPIError,
)


class ScannerClient:
    """Synchronous client for LLM Security Scanner API."""
    
    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        """
        Initialize the scanner client.
        
        Args:
            url: Base URL of the scanner API (e.g., "http://localhost:8000")
            api_key: API key for authentication
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "llm-security-scanner-python/0.1.0",
        })
    
    def scan(
        self,
        prompt: str,
        tenant_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScanResult:
        """
        Scan a prompt for PII and secrets.
        
        Args:
            prompt: The text to scan
            tenant_id: Optional tenant identifier
            metadata: Optional metadata dictionary
            
        Returns:
            ScanResult object with scan results
            
        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            ScannerAPIError: If API returns an error
        """
        payload = {"prompt": prompt}
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if metadata:
            payload["metadata"] = metadata
        
        try:
            response = self._session.post(
                f"{self.url}/scan",
                json=payload,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            
            return self._handle_scan_response(response)
            
        except requests.exceptions.RequestException as e:
            raise ScannerAPIError(f"Request failed: {str(e)}", 0, str(e))
    
    def proxy_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        stop: Optional[Union[str, List[str]]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        """
        Proxy a chat completion request through the security scanner.
        
        Args:
            messages: List of messages with role and content
            model: Model to use (optional, uses default if not specified)
            temperature: Sampling temperature
            max_tokens: Maximum number of tokens to generate
            top_p: Nucleus sampling parameter
            frequency_penalty: Frequency penalty parameter
            presence_penalty: Presence penalty parameter
            stop: Stop sequences
            stream: Whether to stream the response (not supported in MVP)
            **kwargs: Additional OpenAI-compatible parameters
            
        Returns:
            ChatCompletionResponse object
            
        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            ScannerAPIError: If API returns an error
        """
        payload = {"messages": messages, "stream": stream}
        
        if model:
            payload["model"] = model
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        if stop is not None:
            payload["stop"] = stop
            
        # Add any additional kwargs
        payload.update(kwargs)
        
        try:
            response = self._session.post(
                f"{self.url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            
            return self._handle_chat_response(response)
            
        except requests.exceptions.RequestException as e:
            raise ScannerAPIError(f"Request failed: {str(e)}", 0, str(e))
    
    def _handle_scan_response(self, response: requests.Response) -> ScanResult:
        """Handle scan API response."""
        if response.status_code == 401:
            raise AuthenticationError("Invalid API key")
        elif response.status_code == 429:
            retry_after = self._extract_retry_after(response)
            raise RateLimitError("Rate limit exceeded", retry_after)
        elif response.status_code >= 400:
            raise ScannerAPIError(
                f"API error: {response.status_code}",
                response.status_code,
                response.text
            )
        
        data = response.json()
        return ScanResult(
            verdict=data["verdict"],
            reason=data["reason"],
            latency_ms=data["latency_ms"],
            request_id=data["request_id"],
            rules_matched=[RuleMatch(**match) for match in data.get("rules_matched", [])],
            cache_hit=data.get("cache_hit", False),
        )
    
    def _handle_chat_response(self, response: requests.Response) -> ChatCompletionResponse:
        """Handle chat completion API response."""
        if response.status_code == 401:
            raise AuthenticationError("Invalid API key")
        elif response.status_code == 429:
            retry_after = self._extract_retry_after(response)
            raise RateLimitError("Rate limit exceeded", retry_after)
        elif response.status_code >= 400:
            raise ScannerAPIError(
                f"API error: {response.status_code}",
                response.status_code,
                response.text
            )
        
        data = response.json()
        return ChatCompletionResponse(
            id=data["id"],
            object=data.get("object", "chat.completion"),
            created=data["created"],
            model=data["model"],
            choices=[ChatCompletionChoice(**choice) for choice in data["choices"]],
            usage=data.get("usage", ChatCompletionUsage()),
        )
    
    def _extract_retry_after(self, response: requests.Response) -> Optional[int]:
        """Extract retry-after header from response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return int(retry_after)
            except ValueError:
                # If it's a date string, parse it
                try:
                    retry_time = time.strptime(retry_after, "%a, %d %b %Y %H:%M:%S GMT")
                    return int(retry_time - time.gmtime())
                except ValueError:
                    pass
        return None
    
    def close(self) -> None:
        """Close the underlying session."""
        self._session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class AsyncScannerClient:
    """Asynchronous client for LLM Security Scanner API."""
    
    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        """
        Initialize the async scanner client.
        
        Args:
            url: Base URL of the scanner API (e.g., "http://localhost:8000")
            api_key: API key for authentication
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._client = httpx.AsyncClient()
        self._client.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "llm-security-scanner-python/0.1.0",
        })
    
    async def scan(
        self,
        prompt: str,
        tenant_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScanResult:
        """
        Scan a prompt for PII and secrets.
        
        Args:
            prompt: The text to scan
            tenant_id: Optional tenant identifier
            metadata: Optional metadata dictionary
            
        Returns:
            ScanResult object with scan results
            
        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            ScannerAPIError: If API returns an error
        """
        payload = {"prompt": prompt}
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if metadata:
            payload["metadata"] = metadata
        
        try:
            response = await self._client.post(
                f"{self.url}/scan",
                json=payload,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            
            return self._handle_scan_response(response)
            
        except httpx.RequestError as e:
            raise ScannerAPIError(f"Request failed: {str(e)}", 0, str(e))
    
    async def proxy_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        stop: Optional[Union[str, List[str]]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        """
        Proxy a chat completion request through the security scanner.
        
        Args:
            messages: List of messages with role and content
            model: Model to use (optional, uses default if not specified)
            temperature: Sampling temperature
            max_tokens: Maximum number of tokens to generate
            top_p: Nucleus sampling parameter
            frequency_penalty: Frequency penalty parameter
            presence_penalty: Presence penalty parameter
            stop: Stop sequences
            stream: Whether to stream the response (not supported in MVP)
            **kwargs: Additional OpenAI-compatible parameters
            
        Returns:
            ChatCompletionResponse object
            
        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            ScannerAPIError: If API returns an error
        """
        payload = {"messages": messages, "stream": stream}
        
        if model:
            payload["model"] = model
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        if stop is not None:
            payload["stop"] = stop
            
        # Add any additional kwargs
        payload.update(kwargs)
        
        try:
            response = await self._client.post(
                f"{self.url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            
            return self._handle_chat_response(response)
            
        except httpx.RequestError as e:
            raise ScannerAPIError(f"Request failed: {str(e)}", 0, str(e))
    
    def _handle_scan_response(self, response: httpx.Response) -> ScanResult:
        """Handle scan API response."""
        if response.status_code == 401:
            raise AuthenticationError("Invalid API key")
        elif response.status_code == 429:
            retry_after = self._extract_retry_after(response)
            raise RateLimitError("Rate limit exceeded", retry_after)
        elif response.status_code >= 400:
            raise ScannerAPIError(
                f"API error: {response.status_code}",
                response.status_code,
                response.text
            )
        
        data = response.json()
        return ScanResult(
            verdict=data["verdict"],
            reason=data["reason"],
            latency_ms=data["latency_ms"],
            request_id=data["request_id"],
            rules_matched=[RuleMatch(**match) for match in data.get("rules_matched", [])],
            cache_hit=data.get("cache_hit", False),
        )
    
    def _handle_chat_response(self, response: httpx.Response) -> ChatCompletionResponse:
        """Handle chat completion API response."""
        if response.status_code == 401:
            raise AuthenticationError("Invalid API key")
        elif response.status_code == 429:
            retry_after = self._extract_retry_after(response)
            raise RateLimitError("Rate limit exceeded", retry_after)
        elif response.status_code >= 400:
            raise ScannerAPIError(
                f"API error: {response.status_code}",
                response.status_code,
                response.text
            )
        
        data = response.json()
        return ChatCompletionResponse(
            id=data["id"],
            object=data.get("object", "chat.completion"),
            created=data["created"],
            model=data["model"],
            choices=[ChatCompletionChoice(**choice) for choice in data["choices"]],
            usage=data.get("usage", ChatCompletionUsage()),
        )
    
    def _extract_retry_after(self, response: httpx.Response) -> Optional[int]:
        """Extract retry-after header from response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return int(retry_after)
            except ValueError:
                # If it's a date string, parse it
                try:
                    retry_time = time.strptime(retry_after, "%a, %d %b %Y %H:%M:%S GMT")
                    return int(retry_time - time.gmtime())
                except ValueError:
                    pass
        return None
    
    async def close(self) -> None:
        """Close the underlying client."""
        await self._client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()