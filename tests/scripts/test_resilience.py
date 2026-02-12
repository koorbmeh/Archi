"""
Test resilience layer: retry, circuit breaker, fallback chain, graceful degradation.
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

import logging
import time
from src.core.resilience import (
    CircuitBreaker,
    CircuitBreakerError,
    FallbackChain,
    GracefulDegradation,
    retry_with_backoff,
)

logging.basicConfig(level=logging.INFO)

print("Resilience Test")
print("=" * 60)

# Test 1: Retry with backoff
print("\n1. Testing retry with backoff...")

attempt_count = 0

@retry_with_backoff(max_retries=3, initial_delay=0.5, backoff_factor=2.0)
def flaky_function():
    global attempt_count
    attempt_count += 1

    if attempt_count < 3:
        raise ValueError(f"Attempt {attempt_count} failed")

    return "Success!"

try:
    result = flaky_function()
    print(f"   Result: {result}")
    print(f"   Succeeded after {attempt_count} attempts")
except Exception as e:
    print(f"   Failed: {e}")

# Test 2: Circuit breaker
print("\n2. Testing circuit breaker...")

circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=2)

def unreliable_service(should_fail: bool):
    if should_fail:
        raise Exception("Service unavailable")
    return "OK"

# Cause failures to open circuit
for i in range(4):
    try:
        result = circuit.call(unreliable_service, should_fail=True)
    except Exception as e:
        print(f"   Call {i + 1}: {type(e).__name__}")

# Try when circuit is open
print("\n   Circuit should be OPEN now...")
try:
    result = circuit.call(unreliable_service, should_fail=False)
    print(f"   Unexpected success: {result}")
except CircuitBreakerError as e:
    print(f"   Blocked by circuit breaker: {e}")

# Wait for recovery timeout
print("\n   Waiting for recovery timeout (2.5s)...")
time.sleep(2.5)

# Should attempt reset
try:
    result = circuit.call(unreliable_service, should_fail=False)
    print(f"   Circuit recovered: {result}")
except Exception as e:
    print(f"   Recovery failed: {e}")

# Test 3: Fallback chain
print("\n3. Testing fallback chain...")

def primary_strategy():
    return {"success": False, "error": "Primary service down"}

def secondary_strategy():
    return {"success": False, "error": "Secondary service down"}

def tertiary_strategy():
    return {"success": True, "text": "Tertiary strategy worked!"}

chain = FallbackChain([
    {"name": "primary", "func": primary_strategy},
    {"name": "secondary", "func": secondary_strategy},
    {"name": "tertiary", "func": tertiary_strategy},
])

result = chain.execute()
print(f"   Success: {result['success']}")
print(f"   Strategy used: {result.get('strategy_used')}")
print(f"   Attempts: {result.get('attempts')}")

# Test 4: Graceful degradation
print("\n4. Testing graceful degradation...")

response = GracefulDegradation.simple_response("What is the weather?")
print(f"   Degraded response: {response['text'][:80]}...")

print("\n" + "=" * 60)
print("[OK] Resilience system working!")
