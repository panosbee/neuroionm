from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.services.waveforms import build_waveform_lab


async def build_lesson_package(database: AsyncIOMotorDatabase, planner: dict[str, Any]) -> dict[str, Any]:
    node = planner["node"]
    clinical_plan = planner.get("clinical_plan", {})
    channel_plan = clinical_plan.get("channel_plan", [])
    roots = planner.get("roots", [])
    cranial_nerves = planner.get("cranial_nerves", [])
    protocols = planner.get("protocols", [])
    gaps = clinical_plan.get("gaps", [])
    waveforms = build_waveform_lab(planner)

    return {
        "node": node,
        "learning_path": build_learning_path(node, channel_plan, roots, cranial_nerves, protocols, gaps),
        "objectives": build_objectives(channel_plan, roots, cranial_nerves, protocols, gaps, waveforms),
        "competencies": build_competencies(channel_plan, planner.get("pins", []), protocols, gaps, waveforms),
        "checklist": build_checklist(channel_plan, planner.get("pins", []), protocols, gaps, waveforms),
        "checkpoints": await build_checkpoints(database, planner, waveforms),
        "case_scenarios": build_case_scenarios(planner, waveforms),
    }


def build_learning_path(
    node: dict[str, Any],
    channel_plan: list[dict[str, Any]],
    roots: list[str],
    cranial_nerves: list[str],
    protocols: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    domain = node.get("domain") or "academy"
    anatomy_count = len(roots) + len(cranial_nerves)
    return {
        "domain": domain,
        "level": lesson_level(channel_plan, anatomy_count, protocols, gaps),
        "estimated_minutes": min(45, max(12, 8 + len(channel_plan) * 4 + anatomy_count * 3)),
        "requires_review": bool(gaps),
        "sequence": [
            "anatomy",
            "channel_plan",
            "electrode_placement",
            "waveform_interpretation",
            "waveform_quiz",
            "case_response",
            "checkpoint",
        ],
    }


def lesson_level(
    channel_plan: list[dict[str, Any]],
    anatomy_count: int,
    protocols: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> str:
    if gaps:
        return "supervised_review"
    if len(channel_plan) >= 5 or anatomy_count >= 2:
        return "intermediate"
    if protocols:
        return "protocol_driven"
    return "foundational"


def build_objectives(
    channel_plan: list[dict[str, Any]],
    roots: list[str],
    cranial_nerves: list[str],
    protocols: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    waveforms: dict[str, Any],
) -> list[dict[str, str]]:
    anatomy = ", ".join([*roots, *[f"CN {item}" for item in cranial_nerves]]) or "the selected anatomy"
    objectives = [
        {"id": "anatomy", "title": "Identify linked anatomy", "detail": f"Recognize the monitored anatomy for {anatomy}."},
        {"id": "channels", "title": "Build the channel plan", "detail": f"Select and justify {len(channel_plan)} linked recording channels."},
        {"id": "placement", "title": "Place electrodes", "detail": "Use the mapped body targets and laterality before accepting a channel."},
        {"id": "waveforms", "title": "Interpret waveforms", "detail": f"Compare baseline, signal-change and artifact patterns for {waveforms['modality']['label']}."},
    ]
    if protocols:
        objectives.append({"id": "protocols", "title": "Apply protocol evidence", "detail": f"Connect the lesson to {len(protocols)} linked protocol document(s)."})
    if gaps:
        objectives.append({"id": "gaps", "title": "Surface knowledge gaps", "detail": "Name the items that require review before deployment use."})
    return objectives


def build_competencies(
    channel_plan: list[dict[str, Any]],
    pins: list[dict[str, Any]],
    protocols: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    waveforms: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {"id": "anatomy_mapping", "label": "Anatomy mapping", "state": "available", "evidence_count": len(channel_plan)},
        {"id": "electrode_setup", "label": "Electrode setup", "state": "available" if pins else "blocked", "evidence_count": len(pins)},
        {"id": "waveform_interpretation", "label": "Waveform interpretation", "state": "available", "evidence_count": len(waveforms.get("scenarios", []))},
        {"id": "protocol_traceability", "label": "Protocol traceability", "state": "available" if protocols else "review", "evidence_count": len(protocols)},
        {"id": "gap_awareness", "label": "Knowledge-gap awareness", "state": "review" if gaps else "available", "evidence_count": len(gaps)},
    ]


def build_checklist(
    channel_plan: list[dict[str, Any]],
    pins: list[dict[str, Any]],
    protocols: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    waveforms: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {"id": "review_anatomy", "label": "Review linked anatomy and channel coverage", "required": True, "status": "ready" if channel_plan else "review"},
        {"id": "placement_lab", "label": "Complete electrode placement lab", "required": bool(pins), "status": "ready" if pins else "blocked"},
        {"id": "waveform_lab", "label": f"Review {waveforms['modality']['label']} waveform scenarios", "required": True, "status": "ready"},
        {"id": "waveform_quiz", "label": "Pass waveform quiz", "required": True, "status": "ready"},
        {"id": "case_scenarios", "label": "Pass scored case scenario", "required": True, "status": "ready"},
        {"id": "protocol_review", "label": "Review linked protocol/evidence", "required": bool(protocols), "status": "ready" if protocols else "review"},
        {"id": "gap_review", "label": "Acknowledge knowledge-base gaps", "required": bool(gaps), "status": "review" if gaps else "ready"},
    ]


async def build_checkpoints(database: AsyncIOMotorDatabase, planner: dict[str, Any], waveforms: dict[str, Any]) -> list[dict[str, Any]]:
    checkpoints = []
    anatomy_values = [*planner.get("roots", []), *[f"CN {item}" for item in planner.get("cranial_nerves", [])]]
    if anatomy_values:
        checkpoints.append(
            {
                "id": "linked_anatomy",
                "type": "multi_select",
                "prompt": "Select the anatomy linked to this lesson.",
                "options": build_options(anatomy_values, await anatomy_distractors(database, planner, anatomy_values)),
                "correct_values": anatomy_values,
            }
        )

    channels = planner.get("clinical_plan", {}).get("channel_plan", [])
    if channels:
        correct_channels = [channel["lat_name"] for channel in channels[:6]]
        distractors = await muscle_distractors(database, correct_channels)
        checkpoints.append(
            {
                "id": "channel_plan",
                "type": "multi_select",
                "prompt": "Select the recording channels that belong to this lesson.",
                "options": build_options(correct_channels, distractors, label_lookup={channel["lat_name"]: channel.get("name_el") or channel["lat_name"] for channel in channels}),
                "correct_values": correct_channels,
            }
        )

    checkpoints.append(
        {
            "id": "waveform_response",
            "type": "single_select",
            "prompt": "Which waveform scenario must be handled as technical artifact before clinical interpretation?",
            "options": [
                {"value": scenario["scenario_key"], "label": scenario["title"]}
                for scenario in waveforms.get("scenarios", [])
            ],
            "correct_values": ["artifact"],
        }
    )

    warning_scenarios = [scenario for scenario in waveforms.get("scenarios", []) if scenario.get("status") == "warning"]
    if warning_scenarios:
        checkpoints.append(
            {
                "id": "waveform_warning_patterns",
                "type": "multi_select",
                "prompt": "Select the waveform scenarios that require warning/escalation reasoning.",
                "options": [
                    {"value": scenario["scenario_key"], "label": scenario["title"]}
                    for scenario in waveforms.get("scenarios", [])
                ],
                "correct_values": [scenario["scenario_key"] for scenario in warning_scenarios],
            }
        )

    protocols = planner.get("protocols", [])
    if protocols:
        correct_protocols = [protocol.get("key") for protocol in protocols[:3] if protocol.get("key")]
        protocol_options = [
            {"value": protocol.get("key"), "label": protocol.get("title_el") or protocol.get("key")}
            for protocol in protocols[:6]
            if protocol.get("key")
        ]
        protocol_options.extend(
            [
                {"value": "unlinked.freehand", "label": "Freehand interpretation without linked protocol"},
                {"value": "ignore.defaults", "label": "Ignore inherited/domain-default protocols"},
            ]
        )
        checkpoints.append(
            {
                "id": "protocol_traceability",
                "type": "multi_select",
                "prompt": "Select the protocol evidence that should be reviewed for this lesson.",
                "options": protocol_options,
                "correct_values": correct_protocols,
            }
        )

    modality_key = waveforms.get("modality", {}).get("key")
    checkpoints.append(
        {
            "id": "modality_warning_criteria",
            "type": "multi_select",
            "prompt": "Select the safety checks that belong before clinical escalation.",
            "options": warning_criteria_options(modality_key),
            "correct_values": warning_criteria_correct_values(modality_key),
        }
    )

    gaps = planner.get("clinical_plan", {}).get("gaps", [])
    if gaps:
        checkpoints.append(
            {
                "id": "knowledge_gaps",
                "type": "multi_select",
                "prompt": "Select the knowledge-base gap(s) that must be reviewed.",
                "options": [{"value": gap["title"], "label": gap["title"]} for gap in gaps],
                "correct_values": [gap["title"] for gap in gaps],
            }
        )

    return checkpoints


async def anatomy_distractors(database: AsyncIOMotorDatabase, planner: dict[str, Any], correct_values: list[str]) -> list[str]:
    if planner.get("roots"):
        roots = await database.nerve_roots.find({"root": {"$nin": correct_values}}).sort("root", 1).to_list(length=6)
        return [item["root"] for item in roots[:4]]
    cranial = await database.cranial_nerves.find({}).sort("key", 1).to_list(length=12)
    correct_clean = {value.replace("CN ", "") for value in correct_values}
    distractors = [f"CN {item['key']}" for item in cranial if item.get("key") not in correct_clean][:4]
    if len(distractors) >= 4:
        return distractors
    roots = await database.nerve_roots.find({}).sort("root", 1).to_list(length=6)
    for item in roots:
        if item["root"] not in correct_values and item["root"] not in distractors:
            distractors.append(item["root"])
        if len(distractors) >= 4:
            break
    return distractors


async def muscle_distractors(database: AsyncIOMotorDatabase, correct_values: list[str]) -> list[str]:
    items = await database.muscles.find({"lat_name": {"$nin": correct_values}}).sort("lat_name", 1).to_list(length=16)
    return [item["lat_name"] for item in items[:4]]


def build_options(correct_values: list[str], distractors: list[str], label_lookup: dict[str, str] | None = None) -> list[dict[str, str]]:
    label_lookup = label_lookup or {}
    values = []
    for value in [*correct_values, *distractors]:
        if value not in values:
            values.append(value)
    return [{"value": value, "label": label_lookup.get(value, value)} for value in values]


def build_case_scenarios(planner: dict[str, Any], waveforms: dict[str, Any]) -> list[dict[str, Any]]:
    node = planner["node"]
    clinical_plan = planner.get("clinical_plan", {})
    channels = clinical_plan.get("channel_plan", [])
    first_channel = channels[0] if channels else {}
    first_channel_label = first_channel.get("name_el") or first_channel.get("lat_name") or "the first channel"
    gaps = clinical_plan.get("gaps", [])
    return [
        {
            "id": "baseline_setup",
            "title": "Baseline setup",
            "context": f"Prepare {node.get('title_el') or node.get('key')} before the first high-risk step.",
            "expected_actions": [
                "Confirm linked anatomy and laterality.",
                f"Verify electrode placement and functional check for {first_channel_label}.",
                f"Record baseline {waveforms['modality']['label']} waveform metrics.",
            ],
            "action_options": build_case_action_options(
                [
                    "Confirm linked anatomy and laterality.",
                    f"Verify electrode placement and functional check for {first_channel_label}.",
                    f"Record baseline {waveforms['modality']['label']} waveform metrics.",
                ],
                [
                    "Skip baseline because the channel plan is already generated.",
                    "Treat unmapped electrodes as acceptable without functional check.",
                ],
            ),
            "rubric": "Full credit requires anatomy/laterality confirmation, electrode functional check and baseline metrics before the high-risk step.",
        },
        {
            "id": "signal_change_response",
            "title": "Signal change response",
            "context": "A waveform scenario shows amplitude reduction and latency shift compared with baseline.",
            "expected_actions": [
                "Check technical factors before escalation.",
                "Compare each channel against its own baseline.",
                "Correlate with surgical step, anesthetic state and stimulation/recording setup.",
            ],
            "action_options": build_case_action_options(
                [
                    "Check technical factors before escalation.",
                    "Compare each channel against its own baseline.",
                    "Correlate with surgical step, anesthetic state and stimulation/recording setup.",
                ],
                [
                    "Escalate immediately without checking artifact/noise.",
                    "Compare the changed channel only against a different muscle.",
                ],
            ),
            "rubric": "Full credit requires technical-first review, same-channel baseline comparison and clinical/anesthetic correlation.",
        },
        {
            "id": "kb_gap_review",
            "title": "Knowledge-base readiness review",
            "context": "Use the platform readiness and evidence panels before treating a lesson as deployment-ready.",
            "expected_actions": [gap.get("title", "Review linked evidence") for gap in gaps] or ["Confirm no open planner gaps are reported."],
            "action_options": build_case_action_options(
                [gap.get("title", "Review linked evidence") for gap in gaps] or ["Confirm no open planner gaps are reported."],
                ["Mark the lesson deployment-ready without evidence review.", "Ignore inherited/domain protocol coverage."],
            ),
            "rubric": "Full credit requires explicit acknowledgement of planner gaps or confirmation that no open gaps remain.",
        },
    ]


def build_case_action_options(correct_actions: list[str], distractors: list[str]) -> list[dict[str, Any]]:
    options = []
    for action in [*correct_actions, *distractors]:
        if action not in [item["value"] for item in options]:
            options.append({"value": action, "label": action})
    return options


def grade_case_scenario(lesson: dict[str, Any], scenario_id: str, selected_actions: list[str]) -> dict[str, Any]:
    scenario = next((item for item in lesson.get("case_scenarios", []) if item.get("id") == scenario_id), None)
    if not scenario:
        return {
            "scenario_id": scenario_id,
            "score_percent": 0,
            "passed": False,
            "selected_actions": selected_actions,
            "correct_actions": [],
            "missing_actions": [],
            "extra_actions": selected_actions,
            "recommendations": ["Case scenario was not found for this lesson."],
        }
    correct = set(scenario.get("expected_actions", []))
    selected = set(selected_actions)
    correct_selected = len(correct & selected)
    extra_count = len(selected - correct)
    raw_score = (correct_selected / max(len(correct), 1)) * 100 - extra_count * 12
    score = max(0, min(100, round(raw_score)))
    return {
        "scenario_id": scenario_id,
        "title": scenario.get("title"),
        "score_percent": score,
        "passed": score >= 80,
        "selected_actions": sorted(selected),
        "correct_actions": sorted(correct),
        "missing_actions": sorted(correct - selected),
        "extra_actions": sorted(selected - correct),
        "rubric": scenario.get("rubric"),
        "recommendations": build_case_recommendations(scenario, correct - selected, selected - correct),
    }


def build_case_recommendations(scenario: dict[str, Any], missing: set[str], extra: set[str]) -> list[str]:
    recommendations = []
    if missing:
        recommendations.append(f"Repeat '{scenario.get('title')}' and include: {', '.join(sorted(missing))}.")
    if extra:
        recommendations.append(f"Remove unsafe/unrelated action(s): {', '.join(sorted(extra))}.")
    return recommendations or ["Case scenario passed with complete action selection."]


def grade_checkpoint_submission(lesson: dict[str, Any], answers: dict[str, list[str]]) -> dict[str, Any]:
    results = []
    for checkpoint in lesson.get("checkpoints", []):
        correct = set(checkpoint.get("correct_values", []))
        selected = set(answers.get(checkpoint["id"], []))
        passed = selected == correct
        results.append(
            {
                "checkpoint_id": checkpoint["id"],
                "passed": passed,
                "selected_values": sorted(selected),
                "correct_values": sorted(correct),
                "missing_values": sorted(correct - selected),
                "extra_values": sorted(selected - correct),
            }
        )

    passed_count = sum(1 for item in results if item["passed"])
    total_count = len(results)
    score = round((passed_count / total_count) * 100) if total_count else 0
    return {
        "score_percent": score,
        "passed": total_count > 0 and score >= 80,
        "passed_count": passed_count,
        "total_count": total_count,
        "results": results,
        "recommendations": build_grade_recommendations(results),
    }


def build_grade_recommendations(results: list[dict[str, Any]]) -> list[str]:
    recommendations = []
    for result in results:
        if result["passed"]:
            continue
        if result["missing_values"]:
            recommendations.append(f"Review missing item(s): {', '.join(result['missing_values'])}.")
        if result["extra_values"]:
            recommendations.append(f"Remove unrelated item(s): {', '.join(result['extra_values'])}.")
    return recommendations or ["Checkpoint passed. Continue with lab practice and case scenarios."]


def warning_criteria_options(modality_key: str | None) -> list[dict[str, str]]:
    common = [
        {"value": "same_channel_baseline", "label": "Compare against the same channel baseline"},
        {"value": "technical_chain", "label": "Check stimulation/acquisition/electrode technical chain"},
        {"value": "anesthesia_physiology", "label": "Review anesthesia and physiology variables"},
        {"value": "ignore_artifact", "label": "Ignore artifact if the clinical step is high risk"},
    ]
    modality_specific = {
        "ssep": {"value": "latency_amplitude_pair", "label": "Evaluate amplitude and latency together"},
        "triggered_emg": {"value": "threshold_context", "label": "Interpret threshold in anatomy/technique context"},
        "cranial_emg": {"value": "free_running_burst_context", "label": "Correlate bursts with surgical manipulation and stimulation"},
        "mep_emg": {"value": "multi_channel_pattern", "label": "Differentiate focal loss from global suppression"},
    }.get(modality_key or "", {"value": "multi_channel_pattern", "label": "Differentiate focal loss from global suppression"})
    return [modality_specific, *common]


def warning_criteria_correct_values(modality_key: str | None) -> list[str]:
    specific = {
        "ssep": "latency_amplitude_pair",
        "triggered_emg": "threshold_context",
        "cranial_emg": "free_running_burst_context",
        "mep_emg": "multi_channel_pattern",
    }.get(modality_key or "", "multi_channel_pattern")
    return [specific, "same_channel_baseline", "technical_chain", "anesthesia_physiology"]