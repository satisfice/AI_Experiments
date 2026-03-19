# Direct API provider implementation (no LangChain)
# Each provider makes direct HTTP calls or uses official SDKs
# Complete transparency - no hidden state or caching

import os
import logging
import sys
import json
import urllib.request
import urllib.error
from typing import Optional
from dotenv import load_dotenv
from config import get_model_timeout
from utils import format_error, print_error

# Load environment variables from .env file if it exists
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)


def get_provider(model_name: str):
    """
    Get provider type for a model name.
    Returns a callable that takes (model_name, prompt) and returns response text.

    Model name formats:
    - Local Ollama: "llama3.1:8b", "gemma3:12b"
    - OpenAI: "gpt-4", "gpt-3.5-turbo"
    - Anthropic: "claude-opus", "claude-haiku" (defaults to haiku)
    """
    logger.debug(f"Getting provider for model: {model_name}")

    if model_name.startswith("gpt-"):
        logger.info(f"Using OpenAI provider for {model_name}")
        return invoke_openai

    elif model_name.startswith("claude-"):
        logger.info(f"Using Anthropic provider for {model_name}")
        return invoke_anthropic

    else:
        # Assume Ollama for all other models
        logger.info(f"Using Ollama provider for {model_name}")
        return invoke_ollama


def reinitialize_ollama_model(model_name: str, base_url: str = "http://localhost:11434") -> bool:
    """
    Reinitialize an Ollama model by unloading it from memory via keep_alive=0.
    The next generation request will reload it fresh.

    Args:
        model_name: Model name to unload (e.g., "llama3.1:8b")
        base_url: Ollama server base URL

    Returns:
        True if the unload request succeeded, False otherwise.
    """
    url = f"{base_url}/api/generate"
    payload = {
        "model": model_name,
        "prompt": "Hi",
        "stream": False,
        "keep_alive": 0
    }
    try:
        logger.info(f"Reinitializing Ollama model {model_name} (keep_alive=0)")
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            response.read()
        logger.info(f"Ollama model {model_name} unloaded successfully")
        return True
    except Exception as e:
        logger.warning(f"Could not reinitialize Ollama model {model_name}: {type(e).__name__}: {e}")
        return False


def invoke_ollama(model_name: str, prompt: str, timeout: int = 600, base_url: str = "http://localhost:11434") -> str:
    """
    Invoke Ollama model via HTTP API.

    Args:
        model_name: Model name (e.g., "llama3.1:8b")
        prompt: Prompt text to send
        timeout: Request timeout in seconds (default 600)
        base_url: Ollama server base URL

    Returns:
        Model response text

    Raises:
        TimeoutError: If the request times out
        ConnectionError: If cannot connect to Ollama
        ValueError: If Ollama returns an error
    """
    url = f"{base_url}/api/generate"
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False
    }

    try:
        logger.debug(f"Ollama HTTP POST to {url} (timeout={timeout}s)")
        logger.debug(f"Model: {model_name}, Prompt length: {len(prompt)}")

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode('utf-8'))

            if "response" in result:
                logger.debug(f"Ollama response length: {len(result['response'])}")
                return result["response"]
            else:
                raise ValueError(f"Unexpected Ollama response: {result}")

    except urllib.error.URLError as e:
        # urllib wraps socket.timeout in URLError; surface it as TimeoutError
        if isinstance(e.reason, TimeoutError):
            logger.error(f"Ollama request timed out after {timeout}s for {model_name}")
            raise TimeoutError(f"Ollama request timed out after {timeout}s") from e
        logger.error(f"Cannot connect to Ollama at {base_url}")
        msg = f"Cannot connect to Ollama at {base_url}\nIs Ollama running? Try: ollama serve"
        print_error("providers", msg)
        raise ConnectionError(f"Cannot reach Ollama at {base_url}: {e}")

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response from Ollama")
        raise ValueError(f"Ollama returned invalid JSON: {e}")

    except Exception as e:
        logger.error(f"Error invoking Ollama: {type(e).__name__}: {e}")
        print_error("providers", f"{type(e).__name__}: {e}")
        raise


def invoke_openai(model_name: str, prompt: str) -> str:
    """
    Invoke OpenAI model via OpenAI SDK.

    Args:
        model_name: Model name (e.g., "gpt-4")
        prompt: Prompt text to send

    Returns:
        Model response text

    Raises:
        ValueError: If API key not set or model fails
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed. Install with: pip install openai")
        raise ImportError("openai package required for OpenAI models")

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        logger.error("OPENAI_API_KEY environment variable not set")
        raise ValueError("OPENAI_API_KEY environment variable not set")

    try:
        logger.debug(f"OpenAI API call for {model_name}")
        logger.debug(f"Prompt length: {len(prompt)}")

        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=None,  # Use model default
            timeout=600
        )

        content = response.choices[0].message.content
        logger.debug(f"OpenAI response length: {len(content)}")
        return content

    except Exception as e:
        logger.error(f"Error invoking OpenAI: {type(e).__name__}: {e}")
        print_error("providers", f"{type(e).__name__}: {e}")
        raise


def invoke_anthropic(model_name: str, prompt: str) -> str:
    """
    Invoke Anthropic model via Anthropic SDK.

    Args:
        model_name: Model name (e.g., "claude-opus-20250219")
        prompt: Prompt text to send

    Returns:
        Model response text

    Raises:
        ValueError: If API key not set or model fails
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        logger.error("anthropic package not installed. Install with: pip install anthropic")
        raise ImportError("anthropic package required for Anthropic models")

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        logger.error("ANTHROPIC_API_KEY environment variable not set")
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

    try:
        logger.debug(f"Anthropic API call for {model_name}")
        logger.debug(f"Prompt length: {len(prompt)}")

        client = Anthropic(api_key=api_key)

        response = client.messages.create(
            model=model_name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        content = response.content[0].text
        logger.debug(f"Anthropic response length: {len(content)}")
        return content

    except Exception as e:
        logger.error(f"Error invoking Anthropic: {type(e).__name__}: {e}")
        print_error("providers", f"{type(e).__name__}: {e}")
        raise


def generate(model_name: str, prompt: str, provider=None, timeout: int = None) -> str:
    """
    Invoke LLM with prompt and return raw response text.

    For Ollama models the per-model timeout from models.json is used (default 600s)
    unless overridden by the timeout argument.  OpenAI and Anthropic providers use
    their own internal timeout handling.

    Args:
        model_name: Name of the model to invoke
        prompt: Prompt text to send to the model
        provider: Optional pre-computed provider function
        timeout: Override the configured timeout in seconds (None = use models.json value)

    Returns:
        Model response text

    Raises:
        TimeoutError: If an Ollama request times out
        ConnectionError: If cannot reach model service
        ValueError: If model fails or API key missing
    """
    timeout = timeout if timeout is not None else get_model_timeout(model_name)
    logger.debug(f"Generating response for model: {model_name}")
    logger.debug(f"Prompt length: {len(prompt)} characters")
    logger.info(f"Timeout: {timeout} seconds")

    try:
        # Get provider function based on model name
        if provider is None:
            provider = get_provider(model_name)
        else:
            logger.debug("Using provided provider function")

        logger.info(f"Invoking model {model_name}...")

        # Pass the configured timeout to Ollama; other providers handle it internally
        if provider is invoke_ollama:
            response = invoke_ollama(model_name, prompt, timeout=timeout)
        else:
            response = provider(model_name, prompt)

        logger.info(f"Model response received ({len(response)} characters)")
        return response

    except KeyboardInterrupt:
        logger.warning("Generation interrupted by user")
        print_error("providers", "Generation cancelled by user")
        raise

    except TimeoutError:
        # Let the caller handle timeout reporting and retry logic
        raise

    except ConnectionError as e:
        logger.error(f"Connection error with {model_name}: {e}")
        print_error("providers", "Cannot connect to model service")
        raise

    except Exception as e:
        logger.error(f"Error invoking {model_name}: {type(e).__name__}: {e}")
        print_error("providers", f"{type(e).__name__}: {e}")
        raise
