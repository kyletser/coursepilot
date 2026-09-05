#!/usr/bin/env python3
"""Build the versioned, frozen internal acceptance datasets through public APIs.

The source corpus is the repository's CC-BY-4.0 demo material.  Questions are
manually curated and then expanded with a declared controlled-paraphrase rule;
the resulting reports must not describe them as an independent external set.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from seed_demo import ApiClient, SeedError, ensure_account

DATASET_VERSION = 1
STUDENT_EMAIL = "demo-student@example.com"
STUDENT_PASSWORD = "CoursePilot-demo-student-2026!"


@dataclass(frozen=True)
class CourseEvaluationSpec:
    code: str
    retrieval_prompts: dict[str, tuple[str, ...]]
    qa_answerable: tuple[tuple[str, str, str], ...]
    qa_unanswerable: tuple[tuple[str, str], ...]
    routing: tuple[tuple[str, str], ...]
    path_targets: tuple[tuple[str, int], ...]


DS_PROMPTS = {
    "线性表": (
        "线性表描述的是怎样的逻辑关系？",
        "线性表的首元素和末元素分别有什么边界特征？",
        "选择线性表实现前应先判断哪类工作负载？",
        "按位置访问、插入、删除和查找属于哪类结构的常见操作？",
        "逻辑上的前驱后继关系是否要求连续内存？",
        "随机访问和已知位置附近修改之间应如何取舍？",
    ),
    "顺序表": (
        "为什么连续存储可以常数时间按下标访问？",
        "顺序表在中间插入元素时为什么通常需要搬移？",
        "顺序表使用哪两个量维护存储边界？",
        "几何扩容为什么能让连续追加保持稳定的摊还代价？",
        "一次动态扩容为何可能很昂贵？",
        "顺序表删除中间元素会对后续元素产生什么影响？",
    ),
    "链表": (
        "链表怎样在节点引用中表达元素次序？",
        "链表插入是常数时间需要满足什么前提？",
        "只有位置编号时为什么仍要从表头寻找？",
        "链表修改后必须维持哪些关键不变量？",
        "节点在内存中是否必须连续？",
        "怎样避免一次链表修改造成断链或意外成环？",
    ),
    "栈": (
        "后进先出约束具体表示什么？",
        "压栈后哪个元素应成为唯一栈顶？",
        "弹栈操作前必须检查什么？",
        "函数调用记录为什么适合使用栈？",
        "括号匹配和深度优先搜索利用了哪种顺序约束？",
        "判断任务是否适合栈时应观察未完成任务如何恢复？",
    ),
    "队列": (
        "队列分别从哪一端加入和移除元素？",
        "循环数组为何不能用队尾下标较小判断队列为空？",
        "链式队列需要明确维护哪些引用？",
        "广度优先搜索为什么采用先到先处理的结构？",
        "任务缓冲为什么适合先进先出的组织方式？",
        "释放的数组空间怎样被循环下标重新利用？",
    ),
    "树": (
        "树如何通过父子关系表达层次结构？",
        "非根节点的父节点数量有什么约束？",
        "从根到任一节点的简单路径具有什么性质？",
        "节点深度与高度分别怎样定义？",
        "递归处理空树时为什么要明确返回值？",
        "对子树成立的结论怎样组合到当前节点？",
    ),
    "二叉搜索树": (
        "二叉搜索树左右子树的键应满足什么有序条件？",
        "允许重复键时还需要固定什么规则？",
        "查找代价为什么取决于树高？",
        "删除有两个孩子的节点时可以如何处理？",
        "使用中序后继替换后还要执行哪一步？",
        "删除节点时必须保持的核心不变量是什么？",
    ),
    "图": (
        "图由哪些基本元素组成？",
        "图中的边可以带有哪些附加属性？",
        "稀疏图为什么通常选择邻接表？",
        "什么情况下邻接矩阵更合适？",
        "讨论图算法复杂度为什么要同时给出顶点数和边数？",
        "邻接矩阵如何表示任意两个顶点是否相连？",
    ),
    "图的遍历": (
        "遍历带环图时为什么必须记录已访问集合？",
        "无权图中哪种遍历可以得到最少边数路径？",
        "广度优先搜索为什么按距离层次推进？",
        "深度优先搜索可以用哪两种方式实现？",
        "深度优先搜索适合发现哪些结构？",
        "一次完整遍历应怎样限制顶点和邻接边的处理次数？",
    ),
    "排序": (
        "评价排序算法为什么不能只看平均时间？",
        "排序算法还应比较哪些时间与空间性质？",
        "稳定排序保留了相等键记录的什么信息？",
        "多字段排序流水线为什么关注稳定性？",
        "数据能否全部放入内存为何会影响算法选择？",
        "最坏情况和额外空间应如何纳入排序评价？",
    ),
    "归并排序": (
        "归并排序如何递归拆分并重新合并序列？",
        "合并两个有序区间时每一步观察什么？",
        "相等元素优先取哪一侧才能保持稳定？",
        "归并排序的工作量为何表现为对数层数上的线性工作？",
        "归并排序通常需要付出什么空间代价？",
        "稳定性由合并阶段的哪项选择决定？",
    ),
    "快速排序": (
        "快速排序的分区过程围绕什么元素进行？",
        "分区结束后基准处于什么位置？",
        "分区完成后左右区间必须满足什么大小关系？",
        "随机选择基准主要降低哪类风险？",
        "为什么不能把快速排序的平均表现写成无条件保证？",
        "极端不平衡分区会带来什么风险？",
    ),
}

OS_PROMPTS = {
    "进程": (
        "进程与静态程序文件有什么区别？",
        "一个进程包含哪些运行时状态和资源？",
        "操作系统为进程维护哪些信息？",
        "地址空间边界提供了什么隔离作用？",
        "哪些事件会让进程状态发生变化？",
        "为什么进程不能理解成一份静态文件？",
    ),
    "线程": (
        "同一进程中的线程通常共享哪些内容？",
        "每个线程必须各自保存哪些执行状态？",
        "线程共享可变数据为什么需要同步？",
        "线程为何仍依附于进程的资源和保护边界？",
        "线程间通信方便的原因是什么？",
        "为什么线程不是更小的程序文件？",
    ),
    "进程状态": (
        "就绪、运行和阻塞三种状态分别表示什么？",
        "时间片结束会触发怎样的状态转换？",
        "等待事件发生后阻塞进程转入什么状态？",
        "等待处理器与等待外设为什么不能归为一类？",
        "就绪进程还缺少哪个运行条件？",
        "进程状态变化为什么必须对应明确事件？",
    ),
    "上下文切换": (
        "上下文切换为什么要先保存处理器状态？",
        "恢复另一个执行流的目标是什么？",
        "频繁切换为何会产生额外成本？",
        "切换可能扰动哪些缓存结构？",
        "切换本身是否直接完成应用工作？",
        "保存内容为什么取决于体系结构和内核设计？",
    ),
    "调度": (
        "调度器从哪个集合中选择下一项工作？",
        "评价调度策略要同时观察哪些指标？",
        "单一调度指标改善为什么可能损害其他指标？",
        "交互任务和批处理任务的关注点有什么不同？",
        "调度决策还要遵守哪些约束？",
        "公平性为什么需要与吞吐量一起考虑？",
    ),
    "时间片轮转": (
        "时间片轮转怎样给就绪任务分配处理器？",
        "时间片过长时策略会接近什么？",
        "时间片过短为什么增加切换开销？",
        "轮转策略能否保证任务同时完成？",
        "输入输出阻塞是否会被轮转策略消除？",
        "时间片长度如何影响交互响应？",
    ),
    "并发同步": (
        "并发程序结果为什么可能依赖操作交错顺序？",
        "临界区真正保护的对象是什么？",
        "可靠同步方案至少需要说明哪些性质？",
        "条件不满足时线程应如何等待和唤醒？",
        "为什么不能依赖通常先执行到这里的时序假设？",
        "有界等待在同步协议中解决什么问题？",
    ),
    "信号量": (
        "信号量维护什么状态并提供哪些原子操作？",
        "计数信号量怎样表示同类资源数量？",
        "二值信号量可以表达什么？",
        "资源不足时为什么应受控等待而非忙等？",
        "信号量的含义由什么决定？",
        "获取顺序不一致为何仍可能死锁？",
    ),
    "死锁": (
        "死锁中的一组执行流处于什么相互等待状态？",
        "死锁发生的四个必要条件是什么？",
        "系统如何通过破坏条件预防死锁？",
        "限制资源分配可以避开什么状态？",
        "发生死锁后可以怎样检测与恢复？",
        "选择死锁策略要权衡哪些成本？",
    ),
    "虚拟内存": (
        "虚拟内存如何为每个进程提供独立地址视图？",
        "地址映射由哪些主体协作完成？",
        "虚拟内存支持哪些隔离与装入能力？",
        "为什么虚拟页不必始终驻留在物理内存？",
        "页表项需要记录哪些管理信息？",
        "权限检查和地址转换在访问路径中是什么关系？",
    ),
    "分页与地址转换": (
        "虚拟地址被拆成哪两个部分？",
        "页表如何把虚拟页号转换成物理页框？",
        "页内偏移在转换前后为什么保持不变？",
        "地址转换缓存如何减少重复查表？",
        "映射变化时为何要防止使用过期缓存条目？",
        "页大小会同时影响哪些系统指标？",
    ),
    "缺页处理": (
        "访问未驻留虚拟页时会触发什么？",
        "缺页处理为何先检查地址和权限？",
        "合法缺页需要依次完成哪些工作？",
        "非法访问与合法未驻留应如何区分？",
        "处理完成后原指令为什么可以重新执行？",
        "缺页为什么不等同于应用必然崩溃？",
    ),
    "页面置换": (
        "没有空闲页框时系统必须做什么？",
        "时钟算法怎样利用访问位和循环指针？",
        "被修改的牺牲页在复用前通常需要什么操作？",
        "页面置换希望减少哪些未来代价？",
        "近似最近最少使用策略依赖什么历史？",
        "为什么置换算法只能近似未来访问？",
    ),
    "文件系统": (
        "文件系统如何组织持久化数据？",
        "路径解析按什么方式逐段查找名称？",
        "权限检查应放在哪个可信边界？",
        "一次写操作底层可能更新哪些位置？",
        "断电一致性为什么需要专门机制？",
        "目录、元数据与数据块之间是什么关系？",
    ),
    "文件描述符": (
        "文件描述符在进程内是什么？",
        "内核打开文件状态可以包含哪些信息？",
        "同一路径多次打开是否一定共享状态？",
        "复制描述符为何可能共享同一打开状态？",
        "关闭描述符释放的究竟是什么？",
        "仍有引用时内核对象为什么不会立即消失？",
    ),
    "崩溃一致性": (
        "崩溃一致性要保证中断后恢复到什么状态？",
        "日志式方案为什么先记录可重放或可撤销信息？",
        "写时复制方案如何完成最终切换？",
        "两类方案为什么都依赖明确的持久化顺序？",
        "调用写接口为何不等于数据已经安全落盘？",
        "日志恢复和原子切换分别属于哪类思路？",
    ),
}


def _qa_answerable(
    prompts: dict[str, tuple[str, ...]],
) -> tuple[tuple[str, str, str], ...]:
    claims = {
        "线性表": "线性表描述逻辑上的前驱后继关系，不限定连续内存布局。",
        "顺序表": "顺序表按下标访问通常是常数时间，中间插入或删除往往需要移动后续元素。",
        "链表": "链表只有在已持有相邻节点时，插入或删除才只需调整少量引用。",
        "栈": "栈只在栈顶加入和移除元素，遵循后进先出。",
        "队列": "队列从队尾加入、从队首移除，按到达次序处理任务。",
        "树": "除根节点外，树中每个节点有且只有一个父节点。",
        "二叉搜索树": "删除有两个孩子的节点时可用中序后继替换，再删除后继节点。",
        "图": "邻接表适合稀疏图，邻接矩阵适合规模较小或稠密的图。",
        "图的遍历": "图遍历必须记录已访问集合，避免在环中重复扩展。",
        "排序": "评价排序还要考虑最坏情况、额外空间、稳定性和数据规模。",
        "归并排序": "合并时相等元素优先取左侧即可保持稳定性。",
        "快速排序": "分区后基准位于最终位置，左右区间满足约定的大小关系。",
        "进程": "进程是运行中的程序实例，包含地址空间、执行状态和分配的资源。",
        "线程": "同一进程的线程共享部分资源，但各自保存程序计数器、寄存器和栈。",
        "进程状态": "就绪、运行和阻塞由时间片、处理器与等待事件等明确事件转换。",
        "上下文切换": "上下文切换保存并恢复执行状态，本身不直接完成应用工作。",
        "调度": "调度策略需同时权衡响应时间、周转时间、吞吐量和公平性。",
        "时间片轮转": "时间片过长接近先到先服务，过短会提高上下文切换占比。",
        "并发同步": "同步方案应说明互斥、有界等待以及休眠和唤醒规则。",
        "信号量": "信号量提供不可分割的等待与增加操作，资源不足时应受控等待。",
        "死锁": "互斥、占有并等待、不可抢占和循环等待同时成立时才可能死锁。",
        "虚拟内存": "虚拟内存由硬件和操作系统协作完成地址映射与权限检查。",
        "分页与地址转换": "分页把虚拟地址拆成虚拟页号与页内偏移，偏移在转换前后不变。",
        "缺页处理": "内核先区分非法访问和合法缺页，再为合法缺页装入内容并更新映射。",
        "页面置换": "时钟算法通过访问位和循环指针近似访问历史。",
        "文件系统": "一次文件写入可能涉及数据块、元数据和空间分配等多处更新。",
        "文件描述符": "文件描述符是进程内指向内核打开文件状态的小整数索引。",
        "崩溃一致性": "日志式和写时复制方案都依赖明确的持久化顺序。",
    }
    selected: list[tuple[str, str, str]] = []
    concepts = list(prompts)
    for offset in range(2):
        for concept in concepts:
            selected.append((concept, prompts[concept][offset], claims[concept]))
            if len(selected) == 20:
                return tuple(selected)
    raise AssertionError("not enough answerable QA cases")


DS_UNANSWERABLE = (
    ("二叉搜索树", "红黑树插入修复的旋转代码如何实现？"),
    ("二叉搜索树", "B 树节点分裂的完整伪代码是什么？"),
    ("图", "Dijkstra 使用二叉堆时的工程实现代码是什么？"),
    ("图的遍历", "Tarjan 强连通分量算法的 lowlink 如何更新？"),
    ("排序", "TimSort 的 galloping 模式触发阈值是多少？"),
    ("快速排序", "C++ 标准库 introsort 的源码细节是什么？"),
    ("归并排序", "外部归并排序应配置多少路磁盘缓冲？"),
    ("顺序表", "Python list 当前版本的精确扩容公式是什么？"),
    ("链表", "Linux 内核链表宏的源码如何展开？"),
    ("栈", "JVM 栈帧的字节级布局是什么？"),
    ("队列", "无锁 MPMC 队列如何解决 ABA 问题？"),
    ("树", "AVL 树删除后的全部旋转分支是什么？"),
    ("二叉搜索树", "伸展树访问操作的摊还证明怎么写？"),
    ("图", "最小费用最大流的势函数怎样实现？"),
    ("排序", "基数排序处理负数的生产级实现是什么？"),
    ("线性表", "Java ArrayList 在 JDK 25 中的字段布局是什么？"),
    ("图的遍历", "并行 BFS 在 GPU 上的线程块参数怎么设？"),
    ("快速排序", "双轴快速排序的五区间分割源码是什么？"),
    ("归并排序", "自然归并如何检测并反转下降游程？"),
    ("队列", "RabbitMQ 仲裁队列的 Raft 参数如何调优？"),
)

OS_UNANSWERABLE = (
    ("调度", "Linux CFS 的 vruntime 源码在当前内核如何计算？"),
    ("上下文切换", "x86-64 内核切换寄存器的汇编指令序列是什么？"),
    ("线程", "Windows 线程环境块的二进制布局是什么？"),
    ("进程", "PID 命名空间在 Linux 6.18 的源码入口在哪里？"),
    ("信号量", "futex 系统调用的内核哈希桶实现是什么？"),
    ("死锁", "数据库 InnoDB 死锁检测器源码如何遍历等待图？"),
    ("虚拟内存", "五级页表在特定 CPU 上的控制寄存器怎么配置？"),
    ("分页与地址转换", "某型号处理器 TLB 的组相联参数是多少？"),
    ("缺页处理", "Linux do_user_addr_fault 的逐行源码逻辑是什么？"),
    ("页面置换", "Android 最新版本的页面回收水位参数是多少？"),
    ("文件系统", "ext4 inode 在磁盘上的完整字段偏移是什么？"),
    ("文件描述符", "epoll 内核红黑树节点的源码结构是什么？"),
    ("崩溃一致性", "ZFS 意图日志的生产调优参数有哪些？"),
    ("时间片轮转", "Windows 11 当前调度量子具体是多少毫秒？"),
    ("并发同步", "C++ 内存模型的全部 happens-before 规则是什么？"),
    ("进程状态", "Linux TASK_KILLABLE 的内核枚举值是什么？"),
    ("调度", "EDF 实时调度的可调度性完整证明是什么？"),
    ("文件系统", "NTFS MFT 记录头的字节级格式是什么？"),
    ("虚拟内存", "透明大页在当前发行版默认有哪些 sysfs 配置？"),
    ("崩溃一致性", "SQLite WAL checkpoint 的源码锁顺序是什么？"),
)


def _routing(topic: str) -> tuple[tuple[str, str], ...]:
    return (
        (f"{topic}的基本定义是什么？", "TUTOR_QA"),
        (f"请解释{topic}的核心不变量。", "TUTOR_QA"),
        (f"{topic}在课程资料中解决什么问题？", "TUTOR_QA"),
        (f"给我讲清楚{topic}。", "TUTOR_QA"),
        (f"{topic}有哪些关键性质？", "TUTOR_QA"),
        (f"比较{topic}的两种实现思路。", "CONCEPT_COMPARE"),
        (f"{topic}和相邻概念有什么区别？", "CONCEPT_COMPARE"),
        (f"对比{topic}相关的两类方案。", "CONCEPT_COMPARE"),
        (f"分析{topic}两种机制的异同。", "CONCEPT_COMPARE"),
        (f"{topic}方案 A 和方案 B 有什么不同？", "CONCEPT_COMPARE"),
        (f"诊断我学习{topic}时的知识缺口。", "DIAGNOSE"),
        (f"我在{topic}这里总出错，帮我找薄弱点。", "DIAGNOSE"),
        (f"学习{topic}需要哪些前置知识？", "DIAGNOSE"),
        (f"为什么我学不会{topic}，请定位原因。", "DIAGNOSE"),
        (f"检查一下我对{topic}哪里不会。", "DIAGNOSE"),
        (f"给我出五道{topic}练习题。", "QUIZ"),
        (f"用测验检查我对{topic}的掌握。", "QUIZ"),
        (f"考考我{topic}。", "QUIZ"),
        (f"我想做几道{topic}测试题。", "QUIZ"),
        (f"生成一组{topic}小测验。", "QUIZ"),
        (f"给我制定{topic}学习计划。", "LEARNING_PATH"),
        (f"{topic}应该先学什么？", "LEARNING_PATH"),
        (f"安排一下{topic}的学习顺序。", "LEARNING_PATH"),
        (f"生成{topic}学习路径。", "LEARNING_PATH"),
        (f"帮我规划{topic}的复习顺序。", "LEARNING_PATH"),
    )


SPECS = (
    CourseEvaluationSpec(
        code="CP-DEMO-DS",
        retrieval_prompts=DS_PROMPTS,
        qa_answerable=_qa_answerable(DS_PROMPTS),
        qa_unanswerable=DS_UNANSWERABLE,
        routing=_routing("数据结构"),
        path_targets=(
            ("顺序表", 1),
            ("链表", 1),
            ("栈", 1),
            ("队列", 1),
            ("二叉搜索树", 1),
            ("图的遍历", 1),
            ("排序", 1),
            ("归并排序", 1),
            ("归并排序", 2),
            ("快速排序", 1),
            ("快速排序", 2),
            ("树", 1),
            ("图", 1),
            ("线性表", 1),
            ("二叉搜索树", 2),
        ),
    ),
    CourseEvaluationSpec(
        code="CP-DEMO-OS",
        retrieval_prompts=OS_PROMPTS,
        qa_answerable=_qa_answerable(OS_PROMPTS),
        qa_unanswerable=OS_UNANSWERABLE,
        routing=_routing("操作系统"),
        path_targets=(
            ("线程", 1),
            ("进程状态", 1),
            ("上下文切换", 1),
            ("上下文切换", 2),
            ("时间片轮转", 1),
            ("信号量", 1),
            ("死锁", 1),
            ("分页与地址转换", 1),
            ("缺页处理", 1),
            ("缺页处理", 2),
            ("页面置换", 1),
            ("页面置换", 2),
            ("页面置换", 3),
            ("文件描述符", 1),
            ("崩溃一致性", 1),
        ),
    ),
)


def _round_robin_queries(
    prompts: dict[str, tuple[str, ...]], *, count: int
) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    concepts = list(prompts)
    max_questions = max(len(values) for values in prompts.values())
    for offset in range(max_questions):
        for concept in concepts:
            values = prompts[concept]
            if offset < len(values):
                result.append((concept, values[offset], "manual"))
    # The second pass is a declared controlled paraphrase, not an independent label.
    for concept, query, _source in tuple(result):
        result.append(
            (concept, f"请只依据课程材料回答：{query}", "controlled-paraphrase")
        )
        if len(result) >= count:
            break
    if len(result) < count:
        raise SeedError(f"检索题库只有 {len(result)} 条，少于要求的 {count} 条。")
    return result[:count]


def _name_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    concepts = graph.get("concepts")
    if not isinstance(concepts, list):
        raise SeedError("图谱候选响应缺少 concepts。")
    mapping = {
        str(item["name"]): item
        for item in concepts
        if isinstance(item, dict) and item.get("name") and item.get("id")
    }
    return mapping


def _prerequisite_edges(graph: dict[str, Any]) -> list[list[str]]:
    relations = graph.get("relations")
    if not isinstance(relations, list):
        raise SeedError("图谱候选响应缺少 relations。")
    return [
        [str(item["from_candidate_id"]), str(item["to_candidate_id"])]
        for item in relations
        if isinstance(item, dict)
        and item.get("type") == "PREREQUISITE_OF"
        and item.get("from_candidate_id")
        and item.get("to_candidate_id")
    ]


def _teacher_path(
    target_id: str, edges: Iterable[list[str]], *, max_depth: int
) -> list[str]:
    reverse: dict[str, list[str]] = {}
    for source, target in edges:
        reverse.setdefault(target, []).append(source)
    depths: dict[str, int] = {}
    queue = [(target_id, 0)]
    while queue:
        current, depth = queue.pop(0)
        if depth == max_depth:
            continue
        for parent in reverse.get(current, []):
            candidate_depth = depth + 1
            if parent in depths and depths[parent] <= candidate_depth:
                continue
            depths[parent] = candidate_depth
            queue.append((parent, candidate_depth))
    selected = set(depths) | {target_id}
    indegree = {item: 0 for item in selected}
    adjacency = {item: [] for item in selected}
    for source, target in edges:
        if source in selected and target in selected:
            adjacency[source].append(target)
            indegree[target] += 1
    ready = sorted(item for item, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        item = ready.pop(0)
        ordered.append(item)
        for target in adjacency[item]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    return ordered


def _cases(
    spec: CourseEvaluationSpec,
    concepts: dict[str, dict[str, Any]],
    edges: list[list[str]],
    student_id: str,
) -> dict[str, list[dict[str, Any]]]:
    missing = sorted(set(spec.retrieval_prompts).difference(concepts))
    if missing:
        raise SeedError(f"{spec.code} 缺少已审核概念：{', '.join(missing)}")
    retrieval = []
    for index, (concept, query, source) in enumerate(
        _round_robin_queries(spec.retrieval_prompts, count=100), start=1
    ):
        item = concepts[concept]
        retrieval.append(
            {
                "case_key": f"retrieval-{index:03d}",
                "input": {
                    "query": query,
                    "student_id": student_id,
                    "target_concept_ids": [str(item["id"])],
                },
                "expected": {
                    "relevant_chunk_ids": [str(item["source_chunk_id"])],
                    "relevance_grades": {str(item["source_chunk_id"]): 3},
                },
                "labels": {"concept": concept, "authoring": source},
            }
        )

    qa = []
    for index, (concept, query, claim) in enumerate(spec.qa_answerable, start=1):
        item = concepts[concept]
        chunk_id = str(item["source_chunk_id"])
        qa.append(
            {
                "case_key": f"qa-answerable-{index:02d}",
                "input": {"query": query, "student_id": student_id},
                "expected": {
                    "is_answerable": True,
                    "relevant_chunk_ids": [chunk_id],
                    "required_claim_ids": ["claim-1"],
                },
                "labels": {
                    "category": "answerable",
                    "concept": concept,
                    "claims": {
                        "claim-1": {
                            "text": claim,
                            "supporting_chunk_ids": [chunk_id],
                        }
                    },
                },
            }
        )
    for index, (concept, query) in enumerate(spec.qa_unanswerable, start=1):
        item = concepts[concept]
        qa.append(
            {
                "case_key": f"qa-unanswerable-{index:02d}",
                "input": {"query": query, "student_id": student_id},
                "expected": {
                    "is_answerable": False,
                    "relevant_chunk_ids": [str(item["source_chunk_id"])],
                },
                "labels": {
                    "category": "unanswerable",
                    "nearest_concept": concept,
                    "claims": {
                        "unsupported-scope": {
                            "text": query.rstrip("？?"),
                        }
                    },
                },
            }
        )

    routing = [
        {
            "case_key": f"routing-{index:02d}",
            "input": {"query": query},
            "expected": {"expected_intent": intent},
            "labels": {"balanced_intent_set": True},
        }
        for index, (query, intent) in enumerate(spec.routing, start=1)
    ]
    paths = []
    for index, (target_name, max_depth) in enumerate(spec.path_targets, start=1):
        target_id = str(concepts[target_name]["id"])
        paths.append(
            {
                "case_key": f"path-{index:02d}",
                "input": {
                    "student_id": student_id,
                    "target_concept_id": target_id,
                    "max_depth": max_depth,
                },
                "expected": {
                    "approved_prerequisite_edges": edges,
                    "teacher_concept_ids": _teacher_path(
                        target_id, edges, max_depth=max_depth
                    ),
                },
                "labels": {"target_concept": target_name},
            }
        )
    assert len(retrieval) == 100
    assert len(qa) == 40
    assert len(routing) == 25
    assert len(paths) == 15
    return {
        "RETRIEVAL": retrieval,
        "END_TO_END_QA": qa,
        "INTENT_ROUTING": routing,
        "LEARNING_PATH": paths,
    }


def _ensure_frozen_dataset(
    client: ApiClient,
    course_id: str,
    *,
    code: str,
    dataset_type: str,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    name = f"内部正式-{dataset_type}-{code}-v{DATASET_VERSION}"
    datasets = client.call("GET", f"/api/v1/courses/{course_id}/eval-datasets")
    dataset = next(
        (
            item
            for item in datasets
            if isinstance(item, dict) and item.get("name") == name
        ),
        None,
    )
    if dataset is None:
        dataset = client.call(
            "POST",
            f"/api/v1/courses/{course_id}/eval-datasets",
            json_body={
                "name": name,
                "description": (
                    "CoursePilot 内部冻结验收集；基于仓库 CC-BY-4.0 课程语料，"
                    "包含人工编写问题及明确标记的受控改写，不属于外部独立测试。"
                ),
                "type": dataset_type,
            },
        )
    dataset_id = str(dataset["id"])
    existing = client.call("GET", f"/api/v1/eval-datasets/{dataset_id}/cases")
    if dataset.get("status") == "FROZEN":
        if len(existing) != len(cases):
            raise SeedError(f"冻结数据集 {name} 数量不一致，必须创建新版本。")
        return client.call("GET", f"/api/v1/eval-datasets/{dataset_id}")
    existing_by_key = {str(item["case_key"]): item for item in existing}
    desired_keys = {item["case_key"] for item in cases}
    extras = sorted(set(existing_by_key).difference(desired_keys))
    if extras:
        raise SeedError(f"数据集 {name} 含多余用例，必须创建新版本：{extras[:3]}")
    for case in cases:
        stored = existing_by_key.get(case["case_key"])
        desired = {key: case[key] for key in ("input", "expected", "labels")}
        if stored is None:
            client.call(
                "POST", f"/api/v1/eval-datasets/{dataset_id}/cases", json_body=case
            )
        elif any(stored.get(key) != value for key, value in desired.items()):
            client.call(
                "PATCH", f"/api/v1/eval-cases/{stored['id']}", json_body=desired
            )
    return client.call("POST", f"/api/v1/eval-datasets/{dataset_id}/freeze")


def run(args: argparse.Namespace) -> None:
    teacher = ApiClient(args.api_url, timeout=args.timeout)
    student = ApiClient(args.api_url, timeout=args.timeout)
    ensure_account(
        teacher,
        email=args.teacher_email,
        password=args.teacher_password,
        role="TEACHER",
    )
    ensure_account(
        student,
        email=STUDENT_EMAIL,
        password=STUDENT_PASSWORD,
        role="STUDENT",
    )
    student_profile = student.call("GET", "/api/v1/auth/me")
    student_id = str(student_profile["id"])
    courses = teacher.call("GET", "/api/v1/courses")
    by_code = {str(item["code"]): item for item in courses}
    manifest: dict[str, Any] = {
        "schema_version": "coursepilot.internal-eval-manifest/1.0.0",
        "dataset_version": DATASET_VERSION,
        "courses": {},
    }
    for spec in SPECS:
        course = by_code.get(spec.code)
        if course is None:
            raise SeedError(f"缺少课程 {spec.code}，请先运行 scripts/seed_demo.py。")
        course_id = str(course["id"])
        graph = teacher.call(
            "GET", f"/api/v1/courses/{course_id}/graph/candidates?status=APPROVED"
        )
        concepts = _name_map(graph)
        edges = _prerequisite_edges(graph)
        generated = _cases(spec, concepts, edges, student_id)
        course_manifest: dict[str, Any] = {}
        for dataset_type, cases in generated.items():
            frozen = _ensure_frozen_dataset(
                teacher,
                course_id,
                code=spec.code,
                dataset_type=dataset_type,
                cases=cases,
            )
            course_manifest[dataset_type] = {
                "id": frozen["id"],
                "case_count": frozen["case_count"],
                "content_sha256": frozen["content_sha256"],
                "status": frozen["status"],
            }
            print(
                f"{spec.code} {dataset_type}: {frozen['case_count']} cases, "
                f"sha256={frozen['content_sha256']}"
            )
        manifest["courses"][spec.code] = course_manifest
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
