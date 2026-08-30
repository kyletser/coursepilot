#!/usr/bin/env python3
"""Seed the two open CoursePilot demo courses through the public HTTP API only."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_ROOT = REPOSITORY_ROOT / "samples"
READY_STAGES = {"READY_FOR_REVIEW", "PUBLISHED"}
FINAL_STAGES = READY_STAGES | {"FAILED", "CANCELLED"}
DEMO_QUIZ_MODEL = "SOURCE_GROUNDED_DETERMINISTIC_V1"

# Demo-scale, human-authored evaluation annotations. These datasets are DRAFT,
# deliberately small, and are NOT the frozen acceptance sets of spec §11.1;
# no metric is produced or claimed by the seeding script. Retrieval cases are
# bound to real chunk IDs read from the reviewed graph candidates so every
# expected value traces back to the actual served corpus.
DEMO_EVAL_CONTENT: dict[str, dict[str, Any]] = {
    "CP-DEMO-DS": {
        "retrieval": [
            ("顺序表", "顺序表为什么按下标访问是常数时间，而中间插入要移动元素？"),
            ("链表", "链表插入需要满足什么前提才是常数时间？"),
            ("栈", "哪些实际场景可以利用栈的后进先出约束？"),
            ("队列", "广度优先搜索为什么使用队列？"),
            ("二叉搜索树", "二叉搜索树删除有两个孩子的节点时怎么处理？"),
            ("图的遍历", "图的遍历为什么要维护已访问集合？"),
            ("归并排序", "归并排序的稳定性由合并阶段的哪个选择决定？"),
            ("快速排序", "快速排序分区完成后的不变量是什么？"),
        ],
        "routing": [
            ("什么是二叉搜索树？", "TUTOR_QA"),
            ("树的定义是什么？", "TUTOR_QA"),
            ("比较顺序表和链表的异同", "CONCEPT_COMPARE"),
            ("归并排序和快速排序有什么区别？", "CONCEPT_COMPARE"),
            ("诊断一下我在图这一章缺少哪些前置知识", "DIAGNOSE"),
            ("给我出几道关于栈的练习题", "QUIZ"),
            ("测一测我排序这部分掌握得怎么样", "QUIZ"),
            ("帮我安排队列的学习路径", "LEARNING_PATH"),
            ("请为排序章节制定学习和测验计划", "LEARNING_PATH"),
            ("线性表的常见操作包括哪些？", "TUTOR_QA"),
        ],
        "refusal": [
            ("二叉搜索树删除节点时如何保持有序不变量？", True),
            ("归并排序为什么是稳定的？", True),
            ("红黑树旋转的具体实现代码是什么？", False),
            ("2026 年期末考试的评分标准是什么？", False),
        ],
    },
    "CP-DEMO-OS": {
        "retrieval": [
            ("进程状态", "就绪、运行、阻塞三种状态由什么事件触发转换？"),
            ("上下文切换", "上下文切换为什么有额外成本？"),
            ("时间片轮转", "时间片太长或太短分别会带来什么问题？"),
            ("信号量", "信号量的等待操作为什么要进入受控等待？"),
            ("死锁", "死锁发生的四个必要条件是什么？"),
            ("分页与地址转换", "分页如何把虚拟地址转换为物理地址？"),
            ("缺页处理", "缺页处理为什么要先区分非法访问与合法缺页？"),
            ("页面置换", "时钟算法如何利用访问位降低维护成本？"),
            ("文件描述符", "文件描述符和内核打开文件状态是什么关系？"),
            ("崩溃一致性", "日志式方案如何保证崩溃后恢复到结构有效的状态？"),
        ],
        "routing": [
            ("什么是线程？", "TUTOR_QA"),
            ("页表的作用是什么？", "TUTOR_QA"),
            ("进程和线程有什么区别？", "CONCEPT_COMPARE"),
            ("信号量和二值信号量的用法差异是什么？", "CONCEPT_COMPARE"),
            ("诊断我在虚拟内存部分的前置知识缺口", "DIAGNOSE"),
            ("给我出几道死锁相关的练习题", "QUIZ"),
            ("测试一下我并发同步掌握得如何", "QUIZ"),
            ("帮我规划文件系统的学习路径", "LEARNING_PATH"),
            ("为虚拟内存章节制定学习路径和测验", "LEARNING_PATH"),
            ("调度器评价策略时要看哪些指标？", "TUTOR_QA"),
        ],
        "refusal": [
            ("缺页处理的第一步是什么？", True),
            ("时钟算法如何利用访问位？", True),
            ("Linux 内核 CFS 调度器的具体实现细节是什么？", False),
            ("设备驱动的中断处理代码是什么？", False),
        ],
    },
}


class SeedError(RuntimeError):
    """A reproducible, user-actionable demo seed failure."""


class ApiError(SeedError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class CourseSpec:
    template: str
    code: str
    name: str
    description: str
    semester: str
    logical_name: str
    sample_path: Path


@dataclass
class CourseSeed:
    spec: CourseSpec
    course: dict[str, Any]
    invite_code: str | None = None
    upload: dict[str, Any] | None = None
    publication: dict[str, Any] | None = None


COURSES = (
    CourseSpec(
        template="DATA_STRUCTURES",
        code="CP-DEMO-DS",
        name="数据结构（开放演示）",
        description="CoursePilot 原创 CC BY 4.0 微型演示课程。",
        semester="开放演示 2026",
        logical_name="数据结构开放样例",
        sample_path=SAMPLES_ROOT / "data-structures.md",
    ),
    CourseSpec(
        template="OPERATING_SYSTEMS",
        code="CP-DEMO-OS",
        name="操作系统（开放演示）",
        description="CoursePilot 原创 CC BY 4.0 微型演示课程。",
        semester="开放演示 2026",
        logical_name="操作系统开放样例",
        sample_path=SAMPLES_ROOT / "operating-systems.md",
    ),
)


class ApiClient:
    def __init__(self, base_url: str, *, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.access_token: str | None = None

    def call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        multipart: tuple[dict[str, str], str, bytes, str] | None = None,
    ) -> Any:
        if json_body is not None and multipart is not None:
            raise ValueError("json_body and multipart are mutually exclusive")

        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif multipart is not None:
            fields, filename, content, media_type = multipart
            body, content_type = _encode_multipart(
                fields, filename, content, media_type
            )
            headers["Content-Type"] = content_type

        http_request = urlrequest.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlrequest.urlopen(http_request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except urlerror.HTTPError as exc:
            raw = exc.read()
            payload = _decode_json(raw)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                raise ApiError(
                    exc.code,
                    str(error.get("code", "HTTP_ERROR")),
                    str(error.get("message", exc.reason)),
                    error.get("details")
                    if isinstance(error.get("details"), dict)
                    else {},
                ) from None
            raise ApiError(exc.code, "HTTP_ERROR", str(exc.reason)) from None
        except urlerror.URLError as exc:
            raise SeedError(
                f"无法连接 CoursePilot API：{exc.reason}。请确认服务已启动，"
                f"并检查 --api-url（当前为 {self.base_url}）。"
            ) from None

        payload = _decode_json(raw)
        if not isinstance(payload, dict) or "data" not in payload:
            raise SeedError(
                f"API 返回了无法识别的响应（HTTP {status}）；请确认地址指向 CoursePilot API。"
            )
        error = payload.get("error")
        if isinstance(error, dict):
            raise ApiError(
                status,
                str(error.get("code", "API_ERROR")),
                str(error.get("message", "Request failed")),
                error.get("details") if isinstance(error.get("details"), dict) else {},
            )
        return payload["data"]


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _encode_multipart(
    fields: dict[str, str], filename: str, content: bytes, media_type: str
) -> tuple[bytes, str]:
    boundary = f"CoursePilotDemo{uuid.uuid4().hex}"
    delimiter = f"--{boundary}\r\n".encode("ascii")
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            (
                delimiter,
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(
                    "ascii"
                ),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    safe_filename = filename.replace('"', "")
    chunks.extend(
        (
            delimiter,
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{safe_filename}"\r\n'
            ).encode("ascii"),
            f"Content-Type: {media_type}\r\n\r\n".encode("ascii"),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        )
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def ensure_api_ready(client: ApiClient) -> None:
    client.call("GET", "/health/live")
    try:
        client.call("GET", "/health/ready")
    except ApiError as exc:
        raise SeedError(
            "CoursePilot API 进程存活但依赖未就绪。请运行 `docker compose ps`，"
            "并检查 `docker compose logs api worker postgres redis neo4j`。"
            f"就绪检查返回 {exc.code}，详情：{exc.details or exc.message}"
        ) from None


def ensure_account(
    client: ApiClient, *, email: str, password: str, role: str
) -> dict[str, Any]:
    try:
        client.call(
            "POST",
            "/api/v1/auth/register",
            json_body={"email": email, "password": password, "role": role},
        )
        print(f"  已注册 {role.lower()} 演示账号：{email}")
    except ApiError as exc:
        if exc.code != "EMAIL_ALREADY_REGISTERED":
            raise
        print(f"  复用已有演示账号：{email}")

    try:
        tokens = client.call(
            "POST",
            "/api/v1/auth/login",
            json_body={"email": email, "password": password},
        )
    except ApiError as exc:
        if exc.code == "INVALID_CREDENTIALS":
            raise SeedError(
                f"账号 {email} 已存在，但密码与演示脚本不一致。"
                "请通过对应的 --teacher-password/--student-password 参数提供正确密码。"
            ) from None
        raise
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
        raise SeedError("登录响应缺少 access_token。")
    client.access_token = tokens["access_token"]
    me = client.call("GET", "/api/v1/auth/me")
    if not isinstance(me, dict) or me.get("role") != role:
        actual_role = me.get("role") if isinstance(me, dict) else None
        raise SeedError(
            f"账号 {email} 的角色是 {actual_role!r}，演示需要 {role}；角色不能自动切换。"
        )
    return me


def ensure_course(client: ApiClient, spec: CourseSpec) -> CourseSeed:
    courses = client.call("GET", "/api/v1/courses")
    if not isinstance(courses, list):
        raise SeedError("课程列表响应格式不正确。")
    matches = [item for item in courses if item.get("code") == spec.code]
    if len(matches) > 1:
        ids = ", ".join(str(item.get("id")) for item in matches)
        raise SeedError(
            f"发现多个课程代码 {spec.code}（{ids}），无法安全选择；请先在教师端整理重复课程。"
        )
    if matches:
        course = matches[0]
        if course.get("template") != spec.template:
            raise SeedError(
                f"已有课程 {spec.code} 的模板是 {course.get('template')}，"
                f"但样例需要 {spec.template}。"
            )
        if course.get("status") != "ACTIVE":
            course = client.call(
                "PATCH",
                f"/api/v1/courses/{course['id']}",
                json_body={"status": "ACTIVE"},
            )
        print(f"  复用课程 {spec.code}：{course['id']}")
        return CourseSeed(spec=spec, course=course)

    course = client.call(
        "POST",
        "/api/v1/courses",
        json_body={
            "template": spec.template,
            "code": spec.code,
            "name": spec.name,
            "description": spec.description,
            "semester": spec.semester,
        },
    )
    if not isinstance(course, dict) or not isinstance(course.get("invite_code"), str):
        raise SeedError(f"创建课程 {spec.code} 后未收到邀请码。")
    print(f"  已创建课程 {spec.code}：{course['id']}")
    return CourseSeed(spec=spec, course=course, invite_code=course["invite_code"])


def upload_sample(client: ApiClient, seed: CourseSeed) -> dict[str, Any]:
    try:
        content = seed.spec.sample_path.read_bytes()
    except OSError as exc:
        raise SeedError(f"无法读取样例文件 {seed.spec.sample_path}：{exc}") from None
    data = client.call(
        "POST",
        f"/api/v1/courses/{seed.course['id']}/documents",
        multipart=(
            {"logical_name": seed.spec.logical_name},
            seed.spec.sample_path.name,
            content,
            "text/markdown",
        ),
    )
    if not isinstance(data, dict) or not isinstance(data.get("ingestion_job"), dict):
        raise SeedError(f"上传 {seed.spec.sample_path.name} 后缺少入库任务。")
    duplicate = bool(data.get("duplicate"))
    action = "复用相同 SHA-256 的文档版本" if duplicate else "已上传新文档版本"
    version = data.get("latest_version") or {}
    print(
        f"  {action}：{seed.spec.sample_path.name}（version {version.get('version')}）"
    )
    seed.upload = data
    return data


def _model_unavailable_message(job: dict[str, Any]) -> str:
    details = job.get("stage_details") if isinstance(job, dict) else {}
    embedding = details.get("embedding") if isinstance(details, dict) else {}
    reason = embedding.get("reason") if isinstance(embedding, dict) else None
    return (
        "真实 BGE-M3 向量未就绪"
        f"（{reason or 'embedding status is not READY'}）。CoursePilot 不会伪造向量，"
        "因此脚本停止发布。请确认镜像安装了 `[ml]` 依赖，并让 Worker 能访问已缓存的 "
        "BAAI/bge-m3；首次下载可在本地 `.env` 设置 `MODEL_ALLOW_DOWNLOAD=true` 后重新构建/"
        "启动 api 与 worker。若该任务已到 READY_FOR_REVIEW/PENDING，请在模型就绪后使用空的本地"
        "演示库重新运行，或上传一个真实的新文档版本。"
    )


def wait_for_ingestion(
    client: ApiClient,
    job_id: str,
    *,
    timeout_seconds: float,
    queue_timeout_seconds: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    queued_since: float | None = None
    retried = False
    last_stage: str | None = None

    while True:
        job = client.call("GET", f"/api/v1/ingestion-jobs/{job_id}")
        if not isinstance(job, dict):
            raise SeedError(f"入库任务 {job_id} 的响应格式不正确。")
        stage = str(job.get("stage"))
        if stage != last_stage:
            print(f"    入库 {job_id[:8]}：{stage}（{job.get('progress', 0)}%）")
            last_stage = stage

        if stage in READY_STAGES:
            if stage == "READY_FOR_REVIEW":
                details = job.get("stage_details")
                embedding = (
                    details.get("embedding") if isinstance(details, dict) else {}
                )
                if (
                    not isinstance(embedding, dict)
                    or embedding.get("status") != "READY"
                ):
                    raise SeedError(_model_unavailable_message(job))
            return job
        if stage == "CANCELLED":
            raise SeedError(f"入库任务 {job_id} 已取消；脚本不会伪造完成状态。")
        if stage == "FAILED":
            if not retried:
                retried = True
                print(
                    f"    任务失败（{job.get('error_code')}），按安全检查点自动重试一次。"
                )
                try:
                    client.call("POST", f"/api/v1/ingestion-jobs/{job_id}/retry")
                except ApiError as exc:
                    raise SeedError(
                        f"入库任务 {job_id} 无法重试：{exc.code} {exc.message}。"
                        "请检查 `docker compose logs worker api`。"
                    ) from None
                queued_since = time.monotonic()
                continue
            raise SeedError(
                f"入库任务 {job_id} 重试后仍失败：{job.get('error_code')} "
                f"{job.get('error_message') or ''}。请检查 `docker compose logs worker api`；"
                "若日志显示模型依赖或权重不可用，请先准备真实 BGE-M3 再运行。"
            )

        now = time.monotonic()
        if stage == "QUEUED":
            queued_since = queued_since or now
            if now - queued_since >= queue_timeout_seconds:
                raise SeedError(
                    f"入库任务 {job_id} 在 QUEUED 停留超过 {queue_timeout_seconds:g} 秒，"
                    "Worker 可能未运行或未连接 Redis。请检查 `docker compose ps worker` 和 "
                    "`docker compose logs worker redis`。"
                )
        else:
            queued_since = None
        if stage in FINAL_STAGES:
            raise SeedError(f"入库任务 {job_id} 以未知终态 {stage} 结束。")
        if now >= deadline:
            raise SeedError(
                f"等待入库任务 {job_id} 超过 {timeout_seconds:g} 秒（当前 {stage}）。"
                "请检查 Worker、模型下载和资源占用；脚本未修改任务状态。"
            )
        time.sleep(poll_interval)


def _candidate_has_evidence(candidate: dict[str, Any]) -> bool:
    source = candidate.get("source")
    return bool(
        isinstance(candidate.get("evidence"), str)
        and candidate["evidence"].strip()
        and isinstance(candidate.get("source_chunk_id"), str)
        and isinstance(source, dict)
        and source.get("chunk_id") == candidate.get("source_chunk_id")
        and isinstance(source.get("content"), str)
        and source["content"].strip()
    )


def _sample_title(spec: CourseSpec) -> str:
    """Return the casefolded H1 title of the sample document, if any."""

    try:
        text = spec.sample_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    index = 0
    if lines and lines[0].strip() == "---":
        index = 1
        while index < len(lines) and lines[index].strip() not in {"---", "..."}:
            index += 1
        index += 1
    for line in lines[index:]:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip().casefold()
        if stripped:
            break
    return ""


def approve_evidence_backed_candidates(
    client: ApiClient,
    course_id: str,
    *,
    reject_concept_names: frozenset[str] = frozenset(),
) -> tuple[int, int]:
    queue = client.call("GET", f"/api/v1/courses/{course_id}/graph/candidates")
    if not isinstance(queue, dict):
        raise SeedError(f"课程 {course_id} 的图谱候选响应格式不正确。")
    pending_concepts = [
        item for item in queue.get("concepts", []) if item.get("status") == "PENDING"
    ]
    pending_relations = [
        item for item in queue.get("relations", []) if item.get("status") == "PENDING"
    ]
    invalid = [
        f"{kind}:{item.get('id')}"
        for kind, items in (
            ("concept", pending_concepts),
            ("relation", pending_relations),
        )
        for item in items
        if not _candidate_has_evidence(item)
    ]
    if invalid:
        raise SeedError(
            "发现没有完整来源证据的待审核图谱候选，脚本拒绝自动批准："
            + ", ".join(invalid)
        )

    rejected_metadata: list[str] = []
    for candidate in pending_concepts:
        name = str(candidate.get("name", "")).strip().casefold()
        if name in reject_concept_names:
            # Document titles are metadata, not knowledge points; rejecting
            # them (instead of leaving them pending) keeps publication honest
            # and the student-facing graph free of pseudo concepts.
            client.call("POST", f"/api/v1/graph/concepts/{candidate['id']}/reject")
            rejected_metadata.append(str(candidate.get("name")))
            continue
        client.call("POST", f"/api/v1/graph/concepts/{candidate['id']}/approve")
    for candidate in pending_relations:
        endpoint_names = {
            str(candidate.get(key, "")).strip().casefold()
            for key in ("from_concept_name", "to_concept_name")
        }
        if endpoint_names & reject_concept_names:
            client.call("POST", f"/api/v1/graph/relations/{candidate['id']}/reject")
            continue
        try:
            client.call("POST", f"/api/v1/graph/relations/{candidate['id']}/approve")
        except ApiError as exc:
            raise SeedError(
                f"关系候选 {candidate['id']} 未能通过安全审核：{exc.code} {exc.message}。"
                "脚本不会绕过环检测或来源校验。"
            ) from None
    if rejected_metadata:
        print(
            "  已拒绝文档标题类候选（非知识点）："
            + "、".join(dict.fromkeys(rejected_metadata))
        )
    print(
        f"  已审核证据完整的候选：概念 {len(pending_concepts)}，关系 {len(pending_relations)}"
    )
    return len(pending_concepts), len(pending_relations)


def publish_course(client: ApiClient, seed: CourseSeed) -> dict[str, Any]:
    course_id = str(seed.course["id"])
    try:
        publication = client.call("POST", f"/api/v1/courses/{course_id}/graph/publish")
    except ApiError as exc:
        if exc.code == "GRAPH_INDEX_COMPONENTS_NOT_READY":
            raise SeedError(
                _model_unavailable_message(
                    {"stage_details": {"embedding": exc.details}}
                )
            ) from None
        if exc.code == "GRAPH_INDEX_NOT_READY":
            graph = client.call("GET", f"/api/v1/courses/{course_id}/graph")
            job = seed.upload.get("ingestion_job") if seed.upload else None
            if (
                isinstance(graph, dict)
                and graph.get("published_index")
                and isinstance(job, dict)
                and job.get("stage") == "PUBLISHED"
            ):
                print("  复用已经发布的课程索引。")
                publication = graph["published_index"]
            else:
                raise SeedError(
                    f"课程 {course_id} 没有可发布索引，且无法确认样例版本已发布。"
                ) from None
        else:
            raise
    if not isinstance(publication, dict):
        raise SeedError(f"课程 {course_id} 的发布响应格式不正确。")
    seed.publication = publication

    job_id = str(seed.upload["ingestion_job"]["id"])
    refreshed = client.call("GET", f"/api/v1/ingestion-jobs/{job_id}")
    if not isinstance(refreshed, dict) or refreshed.get("stage") != "PUBLISHED":
        raise SeedError(
            f"课程 {course_id} 返回发布成功，但文档任务 {job_id} 未进入 PUBLISHED；"
            "脚本拒绝将其报告为完成。"
        )
    print(
        f"  已发布课程索引 version {publication.get('version')}，文档版本状态已核验。"
    )
    return publication


def ensure_approved_demo_quizzes(client: ApiClient, course_id: str) -> int:
    pending = client.call(
        "GET", f"/api/v1/courses/{course_id}/quizzes/review?status=PENDING"
    )
    approved = client.call(
        "GET", f"/api/v1/courses/{course_id}/quizzes/review?status=APPROVED"
    )
    if not isinstance(pending, list) or not isinstance(approved, list):
        raise SeedError(f"课程 {course_id} 的题目审核队列响应格式不正确。")

    demo_pending = [
        item for item in pending if item.get("generation_model") == DEMO_QUIZ_MODEL
    ]
    demo_approved = [
        item for item in approved if item.get("generation_model") == DEMO_QUIZ_MODEL
    ]
    if not demo_pending and not demo_approved:
        generated = client.call(
            "POST",
            f"/api/v1/courses/{course_id}/quizzes/generate-candidates",
            json_body={},
        )
        if not isinstance(generated, list):
            raise SeedError(f"课程 {course_id} 的候选题生成响应格式不正确。")
        demo_pending = [
            item
            for item in generated
            if item.get("generation_model") == DEMO_QUIZ_MODEL
        ]

    for item in demo_pending:
        options = item.get("options")
        if (
            not isinstance(item.get("id"), str)
            or not isinstance(item.get("source_chunk_id"), str)
            or not isinstance(item.get("answer"), str)
            or not isinstance(options, list)
            or options.count(item["answer"]) != 1
        ):
            raise SeedError(
                f"候选题 {item.get('id')} 缺少唯一答案或课程来源，脚本拒绝自动批准。"
            )
        client.call("POST", f"/api/v1/quizzes/{item['id']}/approve")

    total = len(demo_approved) + len(demo_pending)
    if total == 0:
        raise SeedError(f"课程 {course_id} 没有生成可审核的来源题目。")
    print(f"  已核验并批准来源题目 {total} 道。")
    return total


def _concept_chunk_ids(
    client: ApiClient,
    course_id: str,
    *,
    document_version_id: str,
) -> dict[str, str]:
    """Map approved concepts in the current sample version to source chunks."""

    queue = client.call(
        "GET",
        f"/api/v1/courses/{course_id}/graph/candidates?status=APPROVED",
    )
    if not isinstance(queue, dict) or not isinstance(queue.get("concepts"), list):
        raise SeedError(f"课程 {course_id} 的图谱候选响应格式不正确。")
    mapping: dict[str, str] = {}
    for concept in queue["concepts"]:
        if not isinstance(concept, dict):
            continue
        name = concept.get("name")
        chunk_id = concept.get("source_chunk_id")
        source = concept.get("source")
        document = source.get("document") if isinstance(source, dict) else None
        source_version_id = (
            document.get("version_id") if isinstance(document, dict) else None
        )
        if str(source_version_id) != document_version_id:
            continue
        if isinstance(name, str) and isinstance(chunk_id, str) and chunk_id:
            previous = mapping.setdefault(name, chunk_id)
            if previous != chunk_id:
                raise SeedError(
                    f"课程 {course_id} 的当前文档版本中概念 {name!r} 对应多个来源 Chunk，"
                    "无法生成唯一的检索标注。"
                )
    return mapping


def _ensure_demo_dataset(
    client: ApiClient,
    course_id: str,
    *,
    dataset_name: str,
    dataset_type: str,
    cases: list[dict[str, Any]],
) -> int:
    datasets = client.call("GET", f"/api/v1/courses/{course_id}/eval-datasets")
    if not isinstance(datasets, list):
        raise SeedError(f"课程 {course_id} 的评测数据集列表响应格式不正确。")
    dataset = next(
        (
            item
            for item in datasets
            if isinstance(item, dict) and item.get("name") == dataset_name
        ),
        None,
    )
    if dataset is None:
        dataset = client.call(
            "POST",
            f"/api/v1/courses/{course_id}/eval-datasets",
            json_body={
                "name": dataset_name,
                "description": (
                    "种子脚本创建的演示规模人工标注数据集（DRAFT，非冻结）。"
                    "验收用冻结数据集必须按 spec §11.1 单独构建。"
                ),
                "type": dataset_type,
            },
        )
        if not isinstance(dataset, dict) or not isinstance(dataset.get("id"), str):
            raise SeedError(f"创建数据集 {dataset_name} 后未收到数据集 ID。")
    dataset_id = str(dataset["id"])

    existing = client.call("GET", f"/api/v1/eval-datasets/{dataset_id}/cases")
    if not isinstance(existing, list):
        raise SeedError(f"数据集 {dataset_id} 的用例列表响应格式不正确。")
    existing_by_key = {
        item.get("case_key"): item
        for item in existing
        if isinstance(item, dict) and isinstance(item.get("case_key"), str)
    }
    changed = 0
    for case in cases:
        stored = existing_by_key.get(case["case_key"])
        desired = {
            "input": case["input"],
            "expected": case["expected"],
            "labels": case["labels"],
        }
        if stored is not None and all(
            stored.get(field) == value for field, value in desired.items()
        ):
            continue
        if str(dataset.get("status")) != "DRAFT":
            raise SeedError(
                f"数据集 {dataset_name} 已冻结，但演示标注与当前课程版本不一致；"
                "请保留该冻结版本并创建新的 DRAFT 数据集。"
            )
        if stored is None:
            response = client.call(
                "POST",
                f"/api/v1/eval-datasets/{dataset_id}/cases",
                json_body=case,
            )
        else:
            stored_id = stored.get("id")
            if not isinstance(stored_id, str) or not stored_id:
                raise SeedError(
                    f"数据集 {dataset_name} 的已有用例 {case['case_key']} 缺少 ID。"
                )
            response = client.call(
                "PATCH",
                f"/api/v1/eval-cases/{stored_id}",
                json_body=desired,
            )
        if not isinstance(response, dict) or not response.get("id"):
            raise SeedError(
                f"数据集 {dataset_name} 的用例 {case['case_key']} 写入失败。"
            )
        changed += 1
    return changed


def ensure_eval_datasets(client: ApiClient, seed: CourseSeed) -> dict[str, int]:
    """Create demo-scale DRAFT evaluation datasets with honest annotations.

    Retrieval cases reference the actual source chunk of each reviewed
    concept, so every expected value is traceable to the served corpus.
    Nothing here is frozen and no metric is produced: the acceptance sets of
    spec §11.1 must be built separately.
    """

    course_id = str(seed.course["id"])
    authored = DEMO_EVAL_CONTENT.get(seed.spec.code)
    if not authored:
        return {}
    latest_version = seed.upload.get("latest_version") if seed.upload else None
    document_version_id = (
        latest_version.get("id") if isinstance(latest_version, dict) else None
    )
    if not isinstance(document_version_id, str) or not document_version_id:
        raise SeedError(f"课程 {seed.spec.code} 缺少本次样例文档版本 ID。")
    chunk_ids = _concept_chunk_ids(
        client,
        course_id,
        document_version_id=document_version_id,
    )

    retrieval_cases: list[dict[str, Any]] = []
    for concept_name, query in authored["retrieval"]:
        chunk_id = chunk_ids.get(concept_name)
        if not chunk_id:
            print(f"  跳过检索用例：概念 {concept_name} 不在候选列表中。")
            continue
        retrieval_cases.append(
            {
                "case_key": f"retrieval-{concept_name}",
                "input": {"query": query},
                "expected": {"relevant_chunk_ids": [chunk_id]},
                "labels": {"concept": concept_name},
            }
        )

    routing_cases = [
        {
            "case_key": f"routing-{index:02d}",
            "input": {"query": query},
            "expected": {"expected_intent": intent},
            "labels": {},
        }
        for index, (query, intent) in enumerate(authored["routing"], start=1)
    ]
    refusal_cases = [
        {
            "case_key": f"refusal-{index:02d}",
            "input": {"query": query},
            "expected": {"is_answerable": answerable},
            "labels": {},
        }
        for index, (query, answerable) in enumerate(authored["refusal"], start=1)
    ]

    counts = {
        "retrieval": _ensure_demo_dataset(
            client,
            course_id,
            dataset_name=f"演示检索集-{seed.spec.code}",
            dataset_type="RETRIEVAL",
            cases=retrieval_cases,
        ),
        "routing": _ensure_demo_dataset(
            client,
            course_id,
            dataset_name=f"演示路由集-{seed.spec.code}",
            dataset_type="INTENT_ROUTING",
            cases=routing_cases,
        ),
        "end_to_end": _ensure_demo_dataset(
            client,
            course_id,
            dataset_name=f"演示拒答与引用集-{seed.spec.code}",
            dataset_type="END_TO_END_QA",
            cases=refusal_cases,
        ),
    }
    print(
        "  已创建/更新演示评测数据集（DRAFT，非冻结，不产出任何指标）："
        f"检索 {counts['retrieval']} 条，路由 {counts['routing']} 条，"
        f"拒答 {counts['end_to_end']} 条。"
    )
    return counts


def join_courses(
    teacher_client: ApiClient, student_client: ApiClient, seeds: list[CourseSeed]
) -> None:
    enrolled = student_client.call("GET", "/api/v1/courses")
    if not isinstance(enrolled, list):
        raise SeedError("学生课程列表响应格式不正确。")
    enrolled_ids = {str(item.get("id")) for item in enrolled}

    for seed in seeds:
        course_id = str(seed.course["id"])
        if course_id in enrolled_ids:
            print(f"  学生已加入 {seed.spec.code}，复用现有 Enrollment。")
            continue
        invite_code = seed.invite_code
        if invite_code is None:
            reset = teacher_client.call(
                "POST", f"/api/v1/courses/{course_id}/invite/reset"
            )
            invite_code = reset.get("invite_code") if isinstance(reset, dict) else None
        if not isinstance(invite_code, str):
            raise SeedError(f"课程 {seed.spec.code} 没有可用邀请码。")
        joined = student_client.call(
            "POST",
            "/api/v1/courses/join",
            json_body={"invite_code": invite_code},
        )
        joined_course = joined.get("course") if isinstance(joined, dict) else None
        if (
            not isinstance(joined_course, dict)
            or str(joined_course.get("id")) != course_id
        ):
            raise SeedError(f"邀请码加入结果与课程 {seed.spec.code} 不一致。")
        enrolled_ids.add(course_id)
        print(f"  学生已通过邀请码加入 {seed.spec.code}。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 CoursePilot 公共 API 幂等导入两门原创开放演示课程。"
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("COURSEPILOT_API_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--teacher-email",
        default=os.getenv("COURSEPILOT_DEMO_TEACHER_EMAIL", "demo-teacher@example.com"),
    )
    parser.add_argument(
        "--teacher-password",
        default=os.getenv(
            "COURSEPILOT_DEMO_TEACHER_PASSWORD", "CoursePilot-demo-teacher-2026!"
        ),
    )
    parser.add_argument(
        "--student-email",
        default=os.getenv("COURSEPILOT_DEMO_STUDENT_EMAIL", "demo-student@example.com"),
    )
    parser.add_argument(
        "--student-password",
        default=os.getenv(
            "COURSEPILOT_DEMO_STUDENT_PASSWORD", "CoursePilot-demo-student-2026!"
        ),
    )
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--ingestion-timeout", type=float, default=900.0)
    parser.add_argument("--queue-timeout", type=float, default=60.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    missing = [
        str(spec.sample_path) for spec in COURSES if not spec.sample_path.is_file()
    ]
    if missing:
        raise SeedError("缺少样例文件：" + ", ".join(missing))
    if (
        min(
            args.request_timeout,
            args.ingestion_timeout,
            args.queue_timeout,
            args.poll_interval,
        )
        <= 0
    ):
        raise SeedError("所有 timeout 与 poll interval 参数都必须大于 0。")

    teacher_client = ApiClient(args.api_url, timeout=args.request_timeout)
    student_client = ApiClient(args.api_url, timeout=args.request_timeout)

    print("[1/8] 检查 CoursePilot 服务")
    ensure_api_ready(teacher_client)

    print("[2/8] 准备教师账号与两门课程")
    ensure_account(
        teacher_client,
        email=args.teacher_email,
        password=args.teacher_password,
        role="TEACHER",
    )
    seeds = [ensure_course(teacher_client, spec) for spec in COURSES]

    print("[3/8] 上传开放样例并轮询真实入库任务")
    for seed in seeds:
        upload = upload_sample(teacher_client, seed)
        wait_for_ingestion(
            teacher_client,
            str(upload["ingestion_job"]["id"]),
            timeout_seconds=args.ingestion_timeout,
            queue_timeout_seconds=args.queue_timeout,
            poll_interval=args.poll_interval,
        )

    print("[4/8] 审核证据完整的图谱候选并发布")
    for seed in seeds:
        title = _sample_title(seed.spec)
        approve_evidence_backed_candidates(
            teacher_client,
            str(seed.course["id"]),
            reject_concept_names=frozenset({title}) if title else frozenset(),
        )
        publish_course(teacher_client, seed)

    print("[5/8] 生成并审核来源题目")
    for seed in seeds:
        ensure_approved_demo_quizzes(teacher_client, str(seed.course["id"]))

    print("[6/8] 建立演示规模评测数据集（DRAFT，非冻结，不产出指标）")
    for seed in seeds:
        ensure_eval_datasets(teacher_client, seed)

    print("[7/8] 准备学生账号并通过邀请码加入")
    ensure_account(
        student_client,
        email=args.student_email,
        password=args.student_password,
        role="STUDENT",
    )
    join_courses(teacher_client, student_client, seeds)

    print("[8/8] 完成")
    for seed in seeds:
        version = seed.upload.get("latest_version", {}) if seed.upload else {}
        print(
            f"  - {seed.spec.code}: course={seed.course['id']} "
            f"document_version={version.get('id')} status=PUBLISHED"
        )
    print("  未生成或声称任何评测指标；后续指标必须来自冻结评测运行。")


def main() -> int:
    try:
        run(parse_args())
    except ApiError as exc:
        print(
            f"演示数据导入失败：HTTP {exc.status} {exc.code}: {exc.message}",
            file=sys.stderr,
        )
        if exc.details:
            print(f"详情：{exc.details}", file=sys.stderr)
        return 1
    except SeedError as exc:
        print(f"演示数据导入失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("演示数据导入已由用户中止；未伪造完成状态。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
