// Task-local Operations tools. Loaded by the pinned runtime; no system Node.
export const name = "narrative-task-tools";
export const inject = ["tools", "agents"];

export function apply(ctx, config) {
    const token = process.env.NARRATIVE_TASK_TOOLS_TOKEN;
    if (!token || !config.url?.startsWith("http://127.0.0.1:")) {
        throw new Error("Narrative task tool host is not configured");
    }
    // Host-only control channel, deliberately absent from the model tool catalog.
    let disposed = false;
    ctx.on("dispose", () => { disposed = true; });
    const post = async (path, value) => {
        const response = await fetch(config.url + path, {
            method: "POST", headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token},
            body: JSON.stringify(value),
        });
        if (!response.ok) throw new Error("Narrative control channel unavailable");
        return response.json();
    };
    const poll = async () => {
        while (!disposed) {
            try {
                const command = await post("/control/poll", {});
                if (command) {
                    try {
                        let agent;
                        while (!disposed) {
                            const claim = await post("/control/claim", {id: command.id, nonce: command.nonce});
                            if (!claim.active) break;
                            agent = ctx.agents.get(command.session_id);
                            if (agent && agent.status !== "idle") break;
                            agent = undefined;
                            await new Promise(resolve => setTimeout(resolve, 20));
                        }
                        if (disposed) return;
                        if (agent) agent.cancel({kind: "user"}, {keepInbox: true});
                        await post("/control/result", {id: command.id, nonce: command.nonce, accepted: !!agent});
                    } catch (error) {
                        await post("/control/result", {id: command.id, nonce: command.nonce, accepted: false, error: String(error)});
                    }
                }
            } catch (_) { return; }
        }
    };
    void poll();
    for (const tool of config.tools) {
        ctx.tools.register({
            name: tool.name,
            description: tool.description,
            parameters: tool.parameters,
            output: {
                schema: {type: "object", additionalProperties: true},
                render: (_args, value) => [{type: "text", text: JSON.stringify(value)}],
            },
            async execute(args, exec) {
                if (exec.signal.aborted) throw new Error("Task has been stopped");
                const sessionId = exec.agent?.id;
                const authorityResponse = await fetch(config.url + "/authority", {
                    method: "POST", headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token},
                    body: JSON.stringify({session_id: sessionId}), signal: exec.signal,
                });
                if (!authorityResponse.ok) throw new Error("Task is no longer authorized");
                const authority = await authorityResponse.json();
                const response = await fetch(config.url + "/call", {
                    method: "POST",
                    headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token},
                    body: JSON.stringify({name: tool.name, args, session_id: sessionId, nonce: authority.nonce}),
                    signal: exec.signal,
                });
                const value = await response.json();
                if (!response.ok || !value.ok) {
                    throw new Error(value.error || "Narrative task tool failed");
                }
                return value.result;
            },
        });
    }
}
