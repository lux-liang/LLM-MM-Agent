from .base_agent import BaseAgent
from prompt.template import (TASK_ANALYSIS_PROMPT, TASK_RESULT_PROMPT, TASK_ANSWER_PROMPT, 
                             TASK_FORMULAS_PROMPT, TASK_FORMULAS_CRITIQUE_PROMPT, TASK_FORMULAS_IMPROVEMENT_PROMPT, 
                             TASK_MODELING_PROMPT, TASK_MODELING_CRITIQUE_PROMPT, TASK_MODELING_IMPROVEMENT_PROMPT,
                             TASK_CODING_PROMPT, TASK_CODING_DEBUG_PROMPT, CODE_STRUCTURE_PROMPT, 
                             TASK_RESULT_WITH_CODE_PROMPT)
import sys
import os
import signal
import subprocess
import threading
import json
import re

try:
    import tiktoken
except ImportError:
    tiktoken = None

try:
    import json5
except ImportError:
    json5 = None


class EnvException(Exception):
    def __init__(self, message):
        self.message = message 
    def __str__(self):
        return self.message
    

def _drain_capped_output(stream, output_tail, limit):
    """Continuously drain child output while retaining only a bounded tail."""
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            output_tail.extend(chunk)
            if len(output_tail) > limit:
                del output_tail[:-limit]
    except (OSError, ValueError):
        pass


def _create_windows_job(process):
    """Put a process in a kill-on-close Windows Job Object."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    ):
        kernel32.CloseHandle(job)
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        kernel32.CloseHandle(job)
        raise ctypes.WinError(ctypes.get_last_error())
    return job


def _terminate_process_tree(process, windows_job=None):
    if os.name == "nt":
        if windows_job:
            import ctypes
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(windows_job)
            return None
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        return None
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return windows_job


def execute_script(script_path, work_dir, timeout=600):
    """Execute generated code with an argv list and a hard deadline.

    The old implementation used shell=True and had no timeout. The generated
    file is still untrusted; callers should run this function in a container
    for production workloads.
    """
    work_dir = os.path.realpath(os.path.abspath(work_dir))
    script = os.path.realpath(os.path.abspath(os.path.join(work_dir, script_path)))
    try:
        inside_work_dir = os.path.commonpath([work_dir, script]) == work_dir
    except ValueError:
        inside_work_dir = False
    if not inside_work_dir:
        raise EnvException("script_path must stay inside work_dir")
    if not os.path.isfile(script):
        raise EnvException(f"script does not exist: {script}")

    env = os.environ.copy()
    env.setdefault("CUDA_VISIBLE_DEVICES", "0")
    command = [sys.executable, "-u", script]
    process = None
    windows_job = None
    output_stream = None
    reader = None
    output_tail = bytearray()
    try:
        try:
            output_limit = int(os.getenv("MMAGENT_MAX_LOG_BYTES", "65536"))
        except ValueError:
            output_limit = 65536
        output_limit = max(4096, min(output_limit, 1048576))
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            shell=False,
            cwd=work_dir,
            env=env,
            start_new_session=(os.name != "nt"),
            creationflags=creationflags,
        )
        output_stream = process.stdout
        if os.name == "nt":
            try:
                windows_job = _create_windows_job(process)
            except Exception as exc:
                process.kill()
                process.wait(timeout=5)
                raise EnvException(
                    "could not establish a Windows process-tree boundary: "
                    + str(exc)[:200]
                ) from exc
        reader = threading.Thread(
            target=_drain_capped_output,
            args=(output_stream, output_tail, output_limit),
            daemon=True,
        )
        reader.start()
        try:
            process.wait(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            windows_job = _terminate_process_tree(process, windows_job)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise EnvException(f"execution timed out after {timeout}s: {script}")

        return_code = process.returncode
        # Closing the job/group also removes background descendants that the
        # generated script attempted to leave behind.
        windows_job = _terminate_process_tree(process, windows_job)
        if output_stream:
            output_stream.close()
        if reader:
            reader.join(timeout=2)
        output = bytes(output_tail).decode("utf-8", errors="replace")
        status = "success" if return_code == 0 else f"failed (exit {return_code})"
        return (
            "The script has been executed. "
            f"Status: {status}. Return code: {return_code}\n{output}"
        )
    except EnvException:
        raise
    except Exception as exc:
        raise EnvException(
            f"Something went wrong in executing {script}: {str(exc)[:300]}"
        ) from exc
    finally:
        if windows_job:
            _terminate_process_tree(process, windows_job)
        if output_stream and not output_stream.closed:
            output_stream.close()


class TaskSolver(BaseAgent):
    def __init__(self, llm):
        super().__init__(llm)

    def analysis(self, prompt: str, task_description: str, user_prompt: str = ''):
        prompt = TASK_ANALYSIS_PROMPT.format(prompt=prompt, task_description=task_description, user_prompt=user_prompt).strip()
        return self.llm.generate(prompt)
    
    def formulas_actor(self, prompt: str, data_summary: str, task_description: str, task_analysis: str, modeling_methods: str, user_prompt: str = ''):
        prompt = TASK_FORMULAS_PROMPT.format(prompt=prompt, data_summary=data_summary, task_description=task_description, task_analysis=task_analysis, modeling_methods=modeling_methods, user_prompt=user_prompt).strip()
        return self.llm.generate(prompt)

    def formulas_critic(self, data_summary: str, task_description: str, task_analysis: str, modeling_formulas: str):
        prompt = TASK_FORMULAS_CRITIQUE_PROMPT.format(data_summary=data_summary, task_description=task_description, task_analysis=task_analysis, modeling_formulas=modeling_formulas).strip()
        return self.llm.generate(prompt)
    
    def formulas_improvement(self, data_summary: str, task_description: str, task_analysis: str, modeling_formulas: str, modeling_formulas_critique: str, user_prompt: str = ''):
        prompt = TASK_FORMULAS_IMPROVEMENT_PROMPT.format(data_summary=data_summary, task_description=task_description, task_analysis=task_analysis, modeling_formulas=modeling_formulas, modeling_formulas_critique=modeling_formulas_critique, user_prompt=user_prompt).strip()
        return self.llm.generate(prompt)

    def modeling(self, formulas_prompt: str, modeling_prompt: str, data_summary: str, task_description: str, task_analysis: str, modeling_methods: str, round: int = 1, user_prompt: str = ''):
        formulas = self.formulas_actor(formulas_prompt, data_summary, task_description, task_analysis, modeling_methods, user_prompt)
        for i in range(round):
            formulas_critique = self.formulas_critic(data_summary, task_description, task_analysis, formulas)
            formulas = self.formulas_improvement(data_summary, task_description, task_analysis, formulas, formulas_critique, user_prompt)
        
        modeling_method = self.modeling_actor(modeling_prompt, data_summary, task_description, task_analysis, formulas, user_prompt)
    
        return formulas, modeling_method

    def modeling_actor(self, prompt: str, data_summary: str, task_description: str, task_analysis: str, formulas: str, user_prompt: str = ''):
        prompt = TASK_MODELING_PROMPT.format(prompt=prompt, data_summary=data_summary, task_description=task_description, task_analysis=task_analysis, modeling_formulas=formulas, user_prompt=user_prompt).strip()
        return self.llm.generate(prompt)

    # def modeling_critic(self, task_description: str, task_analysis: str, data_summary: str, formulas: str, modeling_process: str):
    #     prompt = TASK_MODELING_CRITIQUE_PROMPT.format(task_description=task_description, task_analysis=task_analysis, data_summary=data_summary, modeling_formulas=formulas, modeling_process=modeling_process).strip()
    #     return self.llm.generate(prompt)
    
    # def modeling_improvement(self, task_description: str, task_analysis: str, data_summary: str, formulas: str, modeling_process: str, modeling_process_critique: str):
    #     prompt = TASK_MODELING_IMPROVEMENT_PROMPT.format(task_description=task_description, task_analysis=task_analysis, data_summary=data_summary, modeling_formulas=formulas, modeling_process=modeling_process, modeling_process_critique=modeling_process_critique).strip()
    #     return self.llm.generate(prompt)

    # def modeling(self, task_description: str, task_analysis: str, data_summary: str, formulas: str, round: int = 1):
    #     process = self.modeling_actor(task_description, task_analysis, data_summary, formulas)
    #     for i in range(round):
    #         print(f'MODELING Round {i+1}')
    #         process_critique = self.modeling_critic(task_description, task_analysis, data_summary, formulas, process)
    #         process = self.modeling_improvement(task_description, task_analysis, data_summary, formulas, process, process_critique)
    #     return process
    
    @staticmethod
    def _extract_python_code(completion: str) -> str:
        """Extract a Python program from a model response."""
        if not isinstance(completion, str) or not completion.strip():
            raise ValueError("empty code response")
        fence = chr(96) * 3
        fence_re = re.escape(fence)
        match = re.search(
            fence_re + r"(?:python|py)\s*(.*?)" + fence_re,
            completion,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if match is None:
            match = re.search(
                fence_re + r"\s*(.*?)" + fence_re,
                completion,
                flags=re.DOTALL,
            )
        code = match.group(1).strip() if match else completion.strip()
        if code.lower().startswith("python\n"):
            code = code.split("\n", 1)[1].lstrip()
        if not code:
            raise ValueError("model response did not contain executable code")
        return code

    @staticmethod
    def _trim_observation(observation: str, max_tokens: int = 2000) -> str:
        """Keep the tail of a log so the latest exception is retained."""
        try:
            if tiktoken is None:
                raise RuntimeError("tiktoken is unavailable")
            enc = tiktoken.get_encoding("cl100k_base")
            token_ids = enc.encode(observation)
            if len(token_ids) > max_tokens:
                return enc.decode(token_ids[-max_tokens:])
        except Exception:
            pass
        return observation[-12000:]

    @staticmethod
    def _execution_succeeded(observation: str) -> bool:
        return "Status: success. Return code: 0" in (observation or "")

    @staticmethod
    def _execution_result(observation: str) -> str:
        return observation.split("\n", 1)[1] if "\n" in observation else observation

    def _generate_code(self, prompt: str) -> str:
        """Generate and validate code, retrying formatting-only failures."""
        last_error = None
        for attempt in range(1, 6):
            try:
                return self._extract_python_code(self.llm.generate(prompt))
            except Exception as exc:
                last_error = exc
                print(f"Retry {attempt}/5: model response did not contain Python code")
        raise EnvException(
            "Model did not return executable Python after 5 attempts: "
            + str(last_error)
        ) from last_error

    @staticmethod
    def _script_file(work_dir: str, script_name: str) -> str:
        root = os.path.realpath(os.path.abspath(work_dir))
        path = os.path.realpath(os.path.abspath(os.path.join(root, script_name)))
        try:
            valid = os.path.commonpath([root, path]) == root
        except ValueError:
            valid = False
        if not valid:
            raise EnvException("script_name must stay inside work_dir")
        os.makedirs(root, exist_ok=True)
        return path

    def _write_and_execute(self, code: str, script_name: str, work_dir: str):
        path = self._script_file(work_dir, script_name)
        with open(path, "w", encoding="utf-8") as file:
            file.write(code)
        try:
            timeout = int(os.getenv("MMAGENT_CODE_TIMEOUT", "600"))
        except ValueError:
            timeout = 600
        try:
            observation = execute_script(script_name, work_dir, timeout=max(1, timeout))
        except EnvException as exc:
            observation = (
                "The script has been executed. Status: failed. Return code: -1\n"
                + str(exc)
            )
        return self._trim_observation(observation)

    def coding_actor(self, data_file, data_summary, variable_description, task_description: str, task_analysis: str, formulas: str, modeling: str, dependent_file_prompt: str, code_template: str, script_name: str, work_dir: str, user_prompt: str = ''):
        prompt = TASK_CODING_PROMPT.format(data_file=data_file, data_summary=data_summary, variable_description=variable_description, task_description=task_description, task_analysis=task_analysis, modeling_formulas=formulas, modeling_process=modeling, dependent_file_prompt=dependent_file_prompt, code_template=code_template, user_prompt=user_prompt).strip()
        new_content = self._generate_code(prompt)
        return new_content, self._write_and_execute(new_content, script_name, work_dir)
    
    def coding_debugger(self, code_template: str, modeling: str, code: str, observation: str, script_name: str, work_dir: str, user_prompt: str = ''):
        
        prompt = TASK_CODING_DEBUG_PROMPT.format(code_template=code_template, modeling_process=modeling, code=code, observation=observation, user_prompt=user_prompt).strip()
        
        new_content = self._generate_code(prompt)
        return new_content, self._write_and_execute(new_content, script_name, work_dir)
    
    def coding(self, data_file, data_summary, variable_description, task_description: str, task_analysis: str, formulas: str, modeling: str, dependent_file_prompt: str, code_template: str, script_name: str, work_dir: str, try_num: int = 5, round: int = 1, user_prompt: str = ''):
        code = ""
        observation = ""
        for i in range(try_num):
            print("="*10 + f" Try: {i + 1} " + "="*10)
            iteration = 0
            max_iteration = 3
            while iteration < max_iteration:
                print("="*10 + f" Iteration: {iteration + 1} " + "="*10)
                if iteration == 0:
                    try:
                        code, observation = self.coding_actor(data_file, data_summary, variable_description, task_description, task_analysis, formulas, modeling, dependent_file_prompt, code_template, script_name, work_dir, user_prompt)
                    except Exception as exc:
                        observation = f"The script has been executed. Status: failed. Return code: -1\n{exc}"
                    if self._execution_succeeded(observation):
                        return code, True, self._execution_result(observation)
                else:
                    try:
                        code, observation = self.coding_debugger(code_template, modeling, code, observation, script_name, work_dir, user_prompt)
                    except Exception as exc:
                        observation = f"The script has been executed. Status: failed. Return code: -1\n{exc}"
                    if self._execution_succeeded(observation):
                        return code, True, self._execution_result(observation)
                iteration += 1

        return code, False, None

    def result(self, task_description: str, task_analysis: str, task_formulas: str, task_modeling: str, user_prompt: str = '', execution_result: str = ''):
        if execution_result == '':
            prompt = TASK_RESULT_PROMPT.format(task_description=task_description, task_analysis=task_analysis, task_formulas=task_formulas, task_modeling=task_modeling, user_prompt=user_prompt).strip()
        else:
            prompt = TASK_RESULT_WITH_CODE_PROMPT.format(task_description=task_description, task_analysis=task_analysis, task_formulas=task_formulas, task_modeling=task_modeling, user_prompt=user_prompt, execution_result=execution_result).strip()
        return self.llm.generate(prompt)

    def answer(self, task_description: str, task_analysis: str, task_formulas: str, task_modeling: str, task_result: str, user_prompt: str = ''):
        prompt = TASK_ANSWER_PROMPT.format(task_description=task_description, task_analysis=task_analysis, task_formulas=task_formulas, task_modeling=task_modeling, task_result=task_result, user_prompt=user_prompt).strip()
        return self.llm.generate(prompt)

    def extract_code_structure(self, task_id, code: str, save_path: str):
        prompt = CODE_STRUCTURE_PROMPT.format(code=code, save_path=save_path)
        for _ in range(5):
            try:
                structure = self.llm.generate(prompt)
                structure_json = self._parse_code_structure(structure)
                return self._normalize_code_structure(task_id, structure_json, save_path)
            except Exception:
                continue

        return self._fallback_code_structure(save_path)

    def _parse_code_structure(self, text: str):
        match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        structure_string = match.group(1).strip() if match else text.strip()
        try:
            return json.loads(structure_string)
        except json.JSONDecodeError:
            if json5 is None:
                raise
            return json5.loads(structure_string)

    def _normalize_code_structure(self, task_id, structure_json, save_path: str):
        if not isinstance(structure_json, dict):
            return self._fallback_code_structure(save_path)

        structure_json.setdefault("script_path", save_path)
        structure_json.setdefault("class", [])
        structure_json.setdefault("function", [])
        file_outputs = structure_json.get("file_outputs")
        if not isinstance(file_outputs, list):
            file_outputs = []
            structure_json["file_outputs"] = file_outputs

        for item in file_outputs:
            if isinstance(item, dict):
                item["file_description"] = (
                    "This file is generated by code for Task {}. ".format(task_id)
                    + str(item.get("file_description", ""))
                )

        return structure_json

    def _fallback_code_structure(self, save_path: str):
        return {
            "script_path": save_path,
            "class": [],
            "function": [],
            "file_outputs": [],
        }
