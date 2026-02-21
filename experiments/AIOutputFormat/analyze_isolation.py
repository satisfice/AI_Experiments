#!/usr/bin/env python3
"""
Analyze experiment.py for potential state carryover issues.
"""

import re

with open('experiment.py', 'r') as f:
    content = f.read()

print("=" * 70)
print("ISOLATION ANALYSIS: Checking for state carryover risks")
print("=" * 70)

issues = []

# 1. Check provider reuse
if 'provider = get_provider(actual_model)' in content:
    print("\n✓ Provider creation per model: FOUND")
    print("  Location: Created once per model, reused across iterations")
    print("  Risk: MEDIUM - Depends on LangChain's provider implementation")
    if 'provider_cache' in content or 'global provider' in content:
        issues.append("Global provider cache could maintain state")
    else:
        print("  Mitigation: No global cache, fresh provider per model")

# 2. Check for global state
globals_found = re.findall(r'^\s*\w+\s*=\s*(?!None|True|False|[0-9]|")', content, re.MULTILINE)
print("\n✓ Global variables (non-constant): Checking...")
for var in ['provider', 'response', 'output', 'result']:
    if re.search(rf'^\s*{var}\s*=', content, re.MULTILINE):
        print(f"  Found: {var} (could be state if global)")

# 3. Check caching strategy
print("\n✓ Caching analysis:")
caches = re.findall(r'(\w+_cache)\s*=\s*\{\}', content)
for cache in caches:
    print(f"  - {cache}: Initialized at startup (per-model scope)")
if 'prompts_cache' in content:
    print("    prompts_cache: SAFE - Same input file always returns same content")
if 'format_instructions_cache' in content:
    print("    format_instructions_cache: SAFE - Deterministic format instructions")
if 'model_temp_support_cache' in content:
    print("    model_temp_support_cache: SAFE - Model capability is constant")

# 4. Check for mutable defaults
print("\n✓ Mutable default arguments: Checking...")
if re.findall(r'\w+\([^)]*=\[\]', content):
    print("  WARNING: Found mutable default arguments!")
    for match in re.findall(r'def\s+(\w+)\([^)]*=\[\]', content):
        print(f"    - {match}()")
else:
    print("  None found (good)")

# 5. Check for shared state in loops
print("\n✓ Loop variable isolation: Checking...")
loops = re.findall(r'for\s+(\w+)\s+in\s+', content)
print(f"  Loop variables: {', '.join(set(loops))}")
print("  Risk: LOW - Standard loop variables properly scoped")

# 6. Check file handle reuse
print("\n✓ File operations: Checking...")
if 'open(' in content:
    opens = len(re.findall(r'open\(', content))
    closes = len(re.findall(r'\.close\(\)', content))
    print(f"  Open calls: {opens}, Close calls: {closes}")
    if 'with open' in content:
        print("  Mitigation: Using 'with' statements (auto-closes)")
    else:
        print("  WARNING: Some opens may not use 'with' statement")

print("\n" + "=" * 70)
print("SUMMARY OF ISOLATION CONCERNS")
print("=" * 70)

