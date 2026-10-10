import { appendFileSync, writeFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const requestLog = process.env.ALPHADIANA_PI_REQUEST_LOG;
const firingLog = process.env.ALPHADIANA_PI_FIRING_LOG;
const expectedModel = process.env.ALPHADIANA_PI_EXPECTED_MODEL;
const expectedReasoning = process.env.ALPHADIANA_PI_EXPECTED_REASONING;

function append(path: string | undefined, value: unknown): void {
	if (path) appendFileSync(path, `${JSON.stringify(value)}\n`);
}

export default function audit(pi: ExtensionAPI): void {
	if (requestLog) writeFileSync(requestLog, "");
	if (firingLog) writeFileSync(firingLog, "");
	pi.on("before_provider_request", (event) => {
		const payload = event.payload as { model?: unknown; reasoning?: { effort?: unknown } };
		const record = {
			timestamp: Date.now(),
			model: payload.model,
			reasoning: payload.reasoning,
			matchesExpected:
				payload.model === expectedModel && payload.reasoning?.effort === expectedReasoning,
		};
		append(requestLog, record);
	});
	pi.events.on("meta-harness:firing", (data) => append(firingLog, { timestamp: Date.now(), data }));
}
