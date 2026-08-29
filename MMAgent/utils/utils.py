"""Shared file, JSON and output helpers for MM-Agent."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict

import yaml


def read_text_file(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()


def read_json_file(file_path: str) -> Dict:
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_text_file(file_path: str, content: str):
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)


def write_json_file(file_path: str, data: dict) -> Dict:
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as json_file:
        json.dump(data, json_file, indent=4, ensure_ascii=False)
    return data


def parse_llm_output_to_json(output_text: str) -> dict:
    """Parse the first complete JSON object or array in an LLM response."""
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("LLM output is empty")

    candidates = [output_text.strip()]
    fence = chr(96) * 3
    if fence in output_text:
        for block in output_text.split(fence):
            cleaned = block.strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip()
            if cleaned:
                candidates.append(cleaned)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("No valid JSON object found in LLM output")


def json_to_markdown(paper):
    markdown_lines = [
        "## Problem Background",
        paper.get("problem_background", "No background provided.") + "\n",
        "## Problem Requirement",
        paper.get("problem_requirement", "No requirements provided.") + "\n",
        "## Problem Analysis",
        paper.get("problem_analysis", "No analysis provided.") + "\n",
    ]

    if "problem_modeling" in paper:
        markdown_lines.extend(
            ["## Problem Modeling", paper.get("problem_modeling", "No modeling provided.") + "\n"]
        )

    tasks = paper.get("tasks", [])
    if tasks:
        markdown_lines.append("## Tasks\n")
        for idx, task in enumerate(tasks, start=1):
            markdown_lines.extend(
                [
                    f"### Task {idx}",
                    "#### Task Description",
                    str(task.get("task_description", "No description provided.")) + "\n",
                    "#### Task Analysis",
                    str(task.get("task_analysis", "No analysis provided.")) + "\n",
                    "#### Mathematical Formulas",
                ]
            )
            formulas = task.get("mathematical_formulas", "No formulas provided.")
            if isinstance(formulas, list):
                markdown_lines.extend(["$$" + str(formula) + "$$" for formula in formulas])
            else:
                markdown_lines.append("$$" + str(formulas) + "$$")
            markdown_lines.extend(
                [
                    "",
                    "#### Mathematical Modeling Process",
                    str(task.get("mathematical_modeling_process", "No modeling process provided.")) + "\n",
                    "#### Result",
                    str(task.get("result", "No result provided.")) + "\n",
                    "#### Answer",
                    str(task.get("answer", "No answer provided.")) + "\n",
                ]
            )
            charts = task.get("charts", [])
            if charts:
                markdown_lines.append("#### Charts")
                for i, chart in enumerate(charts, start=1):
                    markdown_lines.extend([f"##### Chart {i}", str(chart) + "\n"])

    return "\n".join(markdown_lines)


def json_to_markdown_general(json_data):
    if isinstance(json_data, str):
        json_data = json.loads(json_data)

    def recursive_markdown(data, indent=0):
        prefix = "  " * indent
        if isinstance(data, dict):
            return "".join(
                f"{prefix}### {key}\n{recursive_markdown(value, indent + 1)}"
                for key, value in data.items()
            )
        if isinstance(data, list):
            return "".join(
                f"{prefix}- **Item {index + 1}**\n{recursive_markdown(item, indent + 1)}"
                for index, item in enumerate(data)
            )
        return f"{prefix}- {data}\n"

    return recursive_markdown(json_data)


def save_solution(solution, name, path):
    write_json_file(os.path.join(path, "json", f"{name}.json"), solution)
    write_text_file(
        os.path.join(path, "markdown", f"{name}.md"),
        json_to_markdown(solution),
    )


def mkdir(path):
    os.makedirs(path, exist_ok=True)
    for subdir in ("json", "markdown", "latex", "code", "usage"):
        os.makedirs(os.path.join(path, subdir), exist_ok=True)


def load_config(args, config_path="config.yaml"):
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    config["model_name"] = args.model_name
    config["method_name"] = args.method_name
    return config


def get_info(args):
    problem_path = f"MMBench/problem/{args.task}.json"
    config = load_config(args)
    dataset_dir = os.path.join("MMBench", "dataset", args.task)
    output_dir = os.path.join(
        "MMAgent",
        "output",
        config["method_name"],
        f"{args.task}_{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    )
    mkdir(output_dir)
    print(f"Processing {problem_path}..., config: {config}")
    return problem_path, config, dataset_dir, output_dir
