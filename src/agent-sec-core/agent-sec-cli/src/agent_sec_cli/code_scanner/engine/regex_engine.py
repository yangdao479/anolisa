import re
from typing import List

from agent_sec_cli.code_scanner.models import Finding, RuleDefinition

_SEGMENT_SPLIT = re.compile(r"[;\n|]|&&")


def _match_with_targets(code: str, rule: RuleDefinition) -> list[str]:
    """Segment-level matching for rules with *target_regexes*.

    Splits *code* by command separators (``;``, ``\n``, ``|``, ``&&``),
    then checks each segment for **both** the main regex and at least one
    target regex.  Returns a list of matched segments (empty = no match).
    """
    segments = _SEGMENT_SPLIT.split(code)
    main_pat = re.compile(rule.regex)
    target_pats = [re.compile(t) for t in rule.target_regexes]  # type: ignore[union-attr]
    evidence: list[str] = []
    for seg in segments:
        if main_pat.search(seg) and any(tp.search(seg) for tp in target_pats):
            evidence.append(seg.strip())
    return evidence


def run_regex_rules(code: str, rules: List[RuleDefinition]) -> List[Finding]:
    """Run all regex rules against *code* and return findings.

    Each rule that matches produces exactly one :class:`Finding` whose
    ``evidence`` list contains every matched substring.
    """
    findings: List[Finding] = []
    for rule in rules:
        if rule.target_regexes:
            evidence = _match_with_targets(code, rule)
            if not evidence:
                continue
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    desc_zh=rule.desc_zh,
                    desc_en=rule.desc_en,
                    evidence=evidence,
                )
            )
        else:
            pattern = re.compile(rule.regex)
            matches = list(pattern.finditer(code))
            if not matches:
                continue
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    desc_zh=rule.desc_zh,
                    desc_en=rule.desc_en,
                    evidence=[m.group() for m in matches],
                )
            )
    return findings
