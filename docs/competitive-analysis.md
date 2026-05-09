# Competitive Analysis: Multi-Agent Orchestration Frameworks

**Date:** 2026-03-26
**Subject:** boba-orchestrator positioning vs. open-source multi-agent frameworks

---

## 1. Competitor Profiles

### 1.1 CrewAI

| Dimension | Detail |
|-----------|--------|
| **GitHub** | ~46k stars. 12M+ daily agent executions claimed. |
| **Architecture** | Role-based agents grouped into "Crews" with sequential, parallel, or hierarchical process types. Higher-level "Flows" for production pipelines. |
| **Model support** | Model-agnostic. OpenAI, Anthropic, Gemini, Ollama, any compatible endpoint. Mix models within a single Crew. |
| **Parallel execution** | Yes. Native parallel and hierarchical execution flows. |
| **Security** | Enterprise tier (AMP Suite) offers RBAC, key management, tracing. No open-source prompt injection sanitizer. Security is a paid feature, not a core design principle. |
| **Conversational mode** | No native conversational-with-background-workers mode. Crews run to completion. |
| **vs. boba-orchestrator strengths** | Massive ecosystem, MCP/A2A support, production battle-tested at scale, rich tooling (CLI, studio, templates). |
| **vs. boba-orchestrator weaknesses** | No planner/worker cost split by design. Security is enterprise-tier paywall, not built-in. No Mode B equivalent. Heavy abstraction layer for what could be simple coordination. |

### 1.2 AutoGen / Microsoft Agent Framework

| Dimension | Detail |
|-----------|--------|
| **GitHub** | ~55k stars (AutoGen). Now merging with Semantic Kernel into "Microsoft Agent Framework" (GA targeting Q1 2026). AutoGen itself is in maintenance mode. |
| **Architecture** | Conversation-driven multi-agent chat. Agents communicate via messages. Supports DAG-based parallel execution. Docker-sandboxed code execution. |
| **Model support** | Model-agnostic. Swap GPT, Claude, or local models via config. |
| **Parallel execution** | Yes. DAG-based parallel patterns. |
| **Security** | Docker sandboxing for code execution. No dedicated prompt injection defense layer. Research papers (AutoDefense) exist but are not productized in the framework. |
| **Conversational mode** | Core design is conversational (agent-to-agent chat). Human-in-the-loop supported. But no "background worker while chatting" split. |
| **vs. boba-orchestrator strengths** | Largest community. Microsoft backing. Mature agent-to-agent conversation patterns. .NET support. Strong research lineage. |
| **vs. boba-orchestrator weaknesses** | Framework is in transition (AutoGen -> Agent Framework). Conversation-based architecture has higher token overhead than plan-then-execute. No explicit cost optimization via model tiering. Sandboxing is for code execution, not prompt injection defense. |

### 1.3 LangGraph (LangChain)

| Dimension | Detail |
|-----------|--------|
| **GitHub** | ~27k stars (LangGraph). Part of the broader LangChain ecosystem (~100k+ stars). |
| **Architecture** | Graph-based state machines. Agents as nodes, edges define control flow. Explicit fan-out/fan-in for parallelism. Stateful by design (checkpointing, persistence). |
| **Model support** | Model-agnostic via LangChain integrations. Widest provider coverage of any framework. |
| **Parallel execution** | Yes. First-class fan-out/fan-in graph primitives. |
| **Security** | Graph structure provides some injection resistance (planner output locked as traversal plan, executors scoped to pre-defined tools). But LangChain itself has had CVEs (prompt injection, Cypher injection). No built-in sanitizer. |
| **Conversational mode** | Yes. Stateful agents can maintain conversation context across turns. But no explicit "background workers + live conversation" split. |
| **vs. boba-orchestrator strengths** | Most flexible control flow (arbitrary graphs). Huge integration library. LangSmith for observability. Checkpointing and state persistence. Enterprise deployment options (LangGraph Platform). |
| **vs. boba-orchestrator weaknesses** | Complexity. Graph DSL is powerful but verbose for simple plan-then-execute patterns. Stateful by default (opposite of boba's stateless-git design). LangChain dependency chain is heavy. Known CVE history. No explicit cost-tiered model assignment. |

### 1.4 OpenAI Swarm -> OpenAI Agents SDK

| Dimension | Detail |
|-----------|--------|
| **GitHub** | Swarm: ~20k stars (archived/educational). Agents SDK: ~19k stars and growing. |
| **Architecture** | Swarm was minimal: agents + handoffs, stateless between calls. Agents SDK evolved this into production-grade: agents-as-tools, guardrails, tracing. |
| **Model support** | Claims provider-agnostic (supports 100+ LLMs via Chat Completions API compatibility). In practice, optimized for OpenAI models. |
| **Parallel execution** | Agents SDK supports agents-as-tools pattern for delegation. Not explicitly parallel in the asyncio-gather sense -- more sequential handoff chains. |
| **Security** | Built-in guardrails that run input validation in parallel with agent execution. Fail-fast on validation failure. No prompt injection sanitizer per se. |
| **Conversational mode** | Handoff-based: conversation transfers between specialized agents. No background-worker-while-chatting split. |
| **vs. boba-orchestrator strengths** | Production-grade from OpenAI. Built-in tracing and guardrails. Simple mental model (agents + handoffs). Strong fine-tuning integration. |
| **vs. boba-orchestrator weaknesses** | Handoff architecture is sequential, not parallel-first. Despite "provider-agnostic" claims, the ecosystem assumes OpenAI. No planner/worker cost split. No data plane separation for injection defense. |

### 1.5 Claude Agent SDK (Anthropic)

| Dimension | Detail |
|-----------|--------|
| **GitHub** | Python and TypeScript SDKs available. Star counts not prominently reported; ecosystem is newer. Powers Claude Code (82k+ stars). |
| **Architecture** | Same infrastructure as Claude Code. Main agent + subagents with isolated context windows. Subagents report back summaries, not full context. Sandboxed container execution. |
| **Model support** | Claude-only. No provider abstraction. You can assign different Claude tiers (Opus, Sonnet, Haiku) to subagents. |
| **Parallel execution** | Yes. Subagents run in parallel with isolated contexts. |
| **Security** | Strongest security posture of any framework. Sandboxed containers, process isolation, resource limits, network control, ephemeral filesystems. Per-subagent tool restrictions. |
| **Conversational mode** | Yes, inherits Claude Code's conversational patterns. Subagents work in background while main agent maintains conversation. |
| **vs. boba-orchestrator strengths** | Battle-tested (it IS Claude Code). Best security model. Native model tiering (Opus plans, Haiku executes). Anthropic-maintained. |
| **vs. boba-orchestrator weaknesses** | Claude-locked. No model abstraction -- if you want GPT or Gemini workers, you cannot use this. Proprietary infrastructure dependency. Not designed for git-as-state-store patterns. |

### 1.6 Other Notable Frameworks

| Framework | Stars | Key Angle |
|-----------|-------|-----------|
| **Google ADK** | ~16k | Model-agnostic (despite Gemini optimization). Multi-agent hierarchies. Python/TS/Go/Java. Fast-growing. |
| **AWS Agent Squad** | Smaller | Lightweight orchestrator for routing conversations to specialized agents. |
| **Composio Agent Orchestrator** | Newer | Parallel coding agents, each with own git worktree/branch/PR. Closest architectural cousin to boba-orchestrator. |
| **Ruflo** | Newer | Claude-focused swarm orchestration with enterprise patterns. |

---

## 2. Feature Comparison Matrix

| Feature | boba | CrewAI | AutoGen | LangGraph | OAI Agents SDK | Claude SDK | Google ADK |
|---------|------|--------|---------|-----------|----------------|------------|------------|
| **Model-agnostic** | Yes (protocol) | Yes | Yes | Yes | Mostly | No (Claude) | Mostly |
| **Planner/worker split** | Core design | Optional | No | Manual | No | Yes (implicit) | No |
| **Parallel workers** | asyncio.gather | Yes | DAG | Fan-out/in | Limited | Subagents | Yes |
| **Cost optimization** | Explicit (Opus plans, Sonnet executes) | Manual | Manual | Manual | Manual | Yes (tier assignment) | Manual |
| **Prompt injection defense** | Sanitizer + data plane separation | Enterprise-only | Research only | Structural (graph) | Guardrails | Container sandbox | No |
| **Minimal tool grants** | Per-subtask | Enterprise | No | Scoped | No | Per-subagent | No |
| **Stateless (git-backed)** | Yes | No | No | No (checkpoints) | Stateless calls | No | No |
| **Conversational + background** | Mode B (planned) | No | Chat-based | Stateful graph | Handoffs | Yes | No |
| **Maturity** | Pre-alpha (M1) | Production | Transitioning | Production | Production | Production | GA |
| **Community** | 0 stars | 46k | 55k | 27k | 19k | N/A | 16k |

---

## 3. Competitive Positioning

### Where boba-orchestrator sits

boba-orchestrator occupies a specific niche that no existing framework fills cleanly:

**The "cost-conscious, security-first, stateless orchestrator" slot.**

Most frameworks treat model selection as a user concern -- you pick a model, the framework calls it. boba-orchestrator makes the planner/worker cost split a first-class architectural decision. A $15/MTok model plans; a $3/MTok model executes. This is not a configuration option bolted on later -- it is the core abstraction (PlannerBackend vs. WorkerBackend).

### Unique angles

1. **Explicit cost tiering by architecture, not configuration.** No other framework separates planner and worker at the protocol level. CrewAI lets you assign different models to agents, but there is no structural enforcement of "this agent plans, these agents execute." In boba, the type system enforces it.

2. **Security as a design constraint, not a feature tier.** CrewAI gates security behind enterprise pricing. AutoGen has research papers. LangGraph has structural properties. Only boba and Claude Agent SDK treat prompt injection defense as a non-negotiable architectural layer. boba's data plane separation (read-only fetchers never get write tools) is unique among model-agnostic frameworks.

3. **Git as the only state store.** Every other framework either requires a database (AutoGen, LangGraph checkpointing) or maintains in-memory state. boba's stateless-by-design means any crash is recoverable from the last commit. This is a strong property for autonomous background agents.

4. **Mode B: conversational + parallel workers.** Claude Agent SDK does this natively (it IS Claude Code). No other open-source, model-agnostic framework combines "stay in conversation" with "dispatch background workers." This is the highest-differentiation feature once implemented.

### Closest competitor

**Claude Agent SDK** is architecturally the closest. It has the planner/worker split (main agent + subagents), parallel execution, strong security, and conversational mode. The critical difference: it is Claude-locked. boba-orchestrator's provider abstraction is its reason to exist as a separate project. If you only ever want Claude, use the Claude Agent SDK. If you want the same architecture with GPT, Gemini, Ollama, or mixed providers -- that is boba's value.

**Composio Agent Orchestrator** is the closest in the coding-agent space (parallel agents, git worktrees). But it is focused specifically on coding workflows, not general-purpose orchestration.

### Gaps to fill

1. **MCP/A2A support.** CrewAI and Google ADK already support Model Context Protocol and Agent-to-Agent communication standards. boba should adopt these for tool interoperability.

2. **Observability.** LangSmith (LangGraph), CrewAI Studio, and OpenAI's built-in tracing all provide run visibility. boba currently has none. A lightweight tracing layer (structured logs to file, since we are stateless) would be a differentiator at the "no database required" tier.

3. **Provider implementations beyond Anthropic.** The protocol exists, but only one backend ships. An OpenAI backend and an Ollama backend would validate the abstraction and attract contributors.

4. **Benchmarks.** No framework publishes honest cost-per-task or tokens-per-task benchmarks. boba could own this narrative: "Here is what Opus+Sonnet costs vs. Opus-only for the same task." This would be the strongest possible marketing for the planner/worker split.

---

## 4. Strategic Recommendations

### Short-term (M1-M5, current roadmap)
- Stay focused on the core loop: plan -> decompose -> parallel execute -> merge -> commit. This is the differentiated workflow.
- Implement the security layer (M4) before any external data flows. This is already planned correctly.

### Medium-term (M6-M8)
- Mode B is the killer feature. Prioritize it. No model-agnostic framework does this today.
- Ship an OpenAI provider backend alongside the Anthropic one. Two providers validates the abstraction; one provider is just a wrapper.
- Add structured logging with cost tracking per run. "This task cost $0.12 (Opus: $0.08 planning, Sonnet: $0.04 executing)" is a story no competitor tells.

### Long-term
- MCP tool server support for worker tool access.
- Position as "the framework for cost-conscious autonomous agents." Not competing with CrewAI on ecosystem breadth or LangGraph on graph flexibility. Competing on: runs autonomously, costs less, stays secure.
- The blog post (M8) should lead with the cost comparison angle. That is the hook.

---

## Sources

- [CrewAI GitHub](https://github.com/crewAIInc/crewAI)
- [AutoGen GitHub](https://github.com/microsoft/autogen)
- [Microsoft Agent Framework GitHub](https://github.com/microsoft/agent-framework)
- [LangGraph GitHub](https://github.com/langchain-ai/langgraph)
- [OpenAI Swarm GitHub](https://github.com/openai/swarm)
- [OpenAI Agents SDK GitHub](https://github.com/openai/openai-agents-python)
- [Claude Agent SDK Python GitHub](https://github.com/anthropics/claude-agent-sdk-python)
- [Claude Agent SDK TypeScript GitHub](https://github.com/anthropics/claude-agent-sdk-typescript)
- [Claude Agent SDK Docs](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Google ADK Python GitHub](https://github.com/google/adk-python)
- [Composio Agent Orchestrator GitHub](https://github.com/ComposioHQ/agent-orchestrator)
- [The 2026 AI Agent Framework Decision Guide](https://dev.to/linou518/the-2026-ai-agent-framework-decision-guide-langgraph-vs-crewai-vs-pydantic-ai-b2h)
- [Agent Orchestration Frameworks 2026](https://byteiota.com/agent-orchestration-frameworks-2026-openai-ruflo-swarms/)
- [Top 5 Open-Source Agentic AI Frameworks 2026](https://aimultiple.com/agentic-frameworks)
- [CrewAI 45.9k Stars](https://www.decisioncrafters.com/crewai-multi-agent-orchestration/)
- [AutoGen 54k Stars](https://theagenttimes.com/articles/54660-stars-and-counting-autogens-rise-charts-the-expanding-universe-of-multi-ag)
- [Claude Agent SDK Subagents Docs](https://platform.claude.com/docs/en/agent-sdk/subagents)
- [Claude Agent SDK Secure Deployment](https://platform.claude.com/docs/en/agent-sdk/secure-deployment)
