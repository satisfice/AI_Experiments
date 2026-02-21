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


def invoke_ollama(model_name: str, prompt: str, base_url: str = "http://localhost:11434") -> str:
    """
    Invoke Ollama model via HTTP API.

    Args:
        model_name: Model name (e.g., "llama3.1:8b")
        prompt: Prompt text to send
        base_url: Ollama server base URL

    Returns:
        Model response text

    Raises:
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
        logger.debug(f"Ollama HTTP POST to {url}")
        logger.debug(f"Model: {model_name}, Prompt length: {len(prompt)}")

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=600) as response:
            result = json.loads(response.read().decode('utf-8'))

            if "response" in result:
                logger.debug(f"Ollama response length: {len(result['response'])}")
                return result["response"]
            else:
                raise ValueError(f"Unexpected Ollama response: {result}")

    except urllib.error.URLError as e:
        logger.error(f"Cannot connect to Ollama at {base_url}")
        sys.stderr.write(f"[{model_name}] ERROR: Cannot connect to Ollama\n")
        sys.stderr.write(f"  Is Ollama running? Try: ollama serve\n")
        sys.stderr.flush()
        raise ConnectionError(f"Cannot reach Ollama at {base_url}: {e}")

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response from Ollama")
        raise ValueError(f"Ollama returned invalid JSON: {e}")

    except Exception as e:
        logger.error(f"Error invoking Ollama: {type(e).__name__}: {e}")
        sys.stderr.write(f"[{model_name}] ERROR: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
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
        sys.stderr.write(f"[{model_name}] ERROR: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
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
        sys.stderr.write(f"[{model_name}] ERROR: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
        raise


def generate(model_name: str, prompt: str, provider=None, timeout_seconds: int = 300) -> str:
    """
    Invoke LLM with prompt and return raw response text.

    Note: timeout_seconds parameter kept for API compatibility but enforcement
    happens at OS level. For Ollama, consider system-level timeout configuration.

    Args:
        model_name: Name of the model to invoke
        prompt: Prompt text to send to the model
        provider: Optional pre-computed provider function (for future use)
        timeout_seconds: Timeout for the model invocation (default 300 seconds)

    Returns:
        Model response text

    Raises:
        ConnectionError: If cannot reach model service
        ValueError: If model fails or API key missing
    """
    logger.debug(f"Generating response for model: {model_name}")
    logger.debug(f"Prompt length: {len(prompt)} characters")
    logger.info(f"Timeout parameter: {timeout_seconds} seconds (enforced at OS level)")

    try:
        # Get provider function based on model name
        if provider is None:
            provider = get_provider(model_name)
        else:
            logger.debug("Using provided provider function")

        logger.info(f"Invoking model {model_name}...")
        sys.stderr.write(f"[{model_name}] Waiting for model response...\n")
        sys.stderr.flush()

        # Direct invocation - complete isolation, no state management
        response = provider(model_name, prompt)

        logger.info(f"Model response received ({len(response)} characters)")
        return response

    except KeyboardInterrupt:
        logger.warning("Generation interrupted by user")
        sys.stderr.write(f"[{model_name}] Generation cancelled by user\n")
        sys.stderr.flush()
        raise

    except ConnectionError as e:
        logger.error(f"Connection error with {model_name}: {e}")
        sys.stderr.write(f"[{model_name}] ERROR: Cannot connect to model service\n")
        sys.stderr.flush()
        raise

    except Exception as e:
        logger.error(f"Error invoking {model_name}: {type(e).__name__}: {e}")
        sys.stderr.write(f"[{model_name}] ERROR: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
        raise
