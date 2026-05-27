const fs = require("fs");

function read(path) {
  return fs.readFileSync(path, "utf8");
}

function write(path, text) {
  fs.writeFileSync(path, text, "utf8");
}

function replaceOnce(text, oldText, newText, label) {
  if (!text.includes(oldText)) {
    throw new Error(`Pattern not found for ${label}`);
  }
  return text.replace(oldText, newText);
}

function patchReplyBundle(path) {
  let text = read(path);
  if (text.includes("onReasoningStream: params.opts.onReasoningStream")) {
    console.log(`ALREADY PATCHED reply bundle: ${path}`);
    return;
  }

  text = replaceOnce(
    text,
    "\t\tstreamParams: params.opts.streamParams,\n\t\tagentDir: params.agentDir,",
    "\t\tstreamParams: params.opts.streamParams,\n\t\treasoningLevel: params.opts.reasoningLevel,\n\t\tonReasoningStream: params.opts.onReasoningStream,\n\t\tonReasoningEnd: params.opts.onReasoningEnd,\n\t\tagentDir: params.agentDir,",
    "runAgentAttempt reasoning passthrough",
  );

  write(path, text);
  console.log(`PATCHED reply bundle: ${path}`);
}

function patchGatewayBundle(path) {
  let text = read(path);

  if (!text.includes("\t\tstreamParams: params.streamParams,\n\t\tsessionKey: params.sessionKey,")) {
    text = replaceOnce(
      text,
      "\t\timages: params.prompt.images,\n\t\tsessionKey: params.sessionKey,",
      "\t\timages: params.prompt.images,\n\t\tstreamParams: params.streamParams,\n\t\tsessionKey: params.sessionKey,",
      "gateway command stream params passthrough",
    );
  }

  if (!text.includes("reasoningLevel: \"stream\"")) {
    text = replaceOnce(
      text,
      "\t\tbestEffortDeliver: false,\n\t\tsenderIsOwner: true\n\t};",
      "\t\tbestEffortDeliver: false,\n\t\tsenderIsOwner: true,\n\t\treasoningLevel: \"stream\",\n\t\tonReasoningStream: params.onReasoningStream,\n\t\tonReasoningEnd: params.onReasoningEnd\n\t};",
      "gateway command reasoning options",
    );
  }

  if (!text.includes("function writeAssistantDeltaChunk")) {
    text = replaceOnce(
      text,
      "function asMessages(val) {",
      `function writeAssistantDeltaChunk(res, params) {
\tconst delta = {};
\tif (typeof params.content === "string") delta.content = params.content;
\tif (typeof params.reasoningContent === "string") delta.reasoning_content = params.reasoningContent;
\twriteSse(res, {
\t\tid: params.runId,
\t\tobject: "chat.completion.chunk",
\t\tcreated: Math.floor(Date.now() / 1e3),
\t\tmodel: params.model,
\t\tchoices: [{
\t\t\tindex: 0,
\t\t\tdelta,
\t\t\tfinish_reason: params.finishReason
\t\t}]
\t});
}
function resolveReasoningStreamDeltaText(evt) {
\tconst delta = evt.data.delta;
\tconst text = evt.data.text;
\treturn typeof delta === "string" ? delta : typeof text === "string" ? text : "";
}
function sliceLongestCommonPrefixDelta(previous, next) {
\tlet prefixLength = 0;
\tconst max = Math.min(previous.length, next.length);
\twhile (prefixLength < max && previous[prefixLength] === next[prefixLength]) prefixLength += 1;
\treturn next.slice(prefixLength);
}
function asMessages(val) {`,
      "assistant delta writer helpers",
    );
  }

  if (!text.includes("let reasoningContent = \"\";")) {
    text = replaceOnce(
      text,
      "\tconst runId = `chatcmpl_${randomUUID()}`;\n\tconst deps = createDefaultDeps();\n\tconst commandInput = buildAgentCommandInput({",
      "\tconst runId = `chatcmpl_${randomUUID()}`;\n\tconst deps = createDefaultDeps();\n\tlet reasoningContent = \"\";\n\tconst commandInput = buildAgentCommandInput({",
      "gateway reasoning accumulator",
    );
  }

  if (!text.includes("const requestedMaxTokens = typeof payload.max_tokens === \"number\"")) {
    text = replaceOnce(
      text,
      "\tconst stream = Boolean(payload.stream);\n\tconst model = typeof payload.model === \"string\" ? payload.model : \"openclaw\";",
      "\tconst stream = Boolean(payload.stream);\n\tconst model = typeof payload.model === \"string\" ? payload.model : \"openclaw\";\n\tconst requestedMaxTokens = typeof payload.max_tokens === \"number\" ? payload.max_tokens : typeof payload.max_completion_tokens === \"number\" ? payload.max_completion_tokens : void 0;\n\tconst streamParams = typeof requestedMaxTokens === \"number\" ? { maxTokens: requestedMaxTokens } : void 0;",
      "chat completions max token stream params",
    );
  }

  if (!text.includes("reasoningContent = payload.text;")) {
    text = replaceOnce(
      text,
      "\t\tsessionKey,\n\t\trunId,\n\t\tmessageChannel\n\t});",
      `\t\tsessionKey,
\t\trunId,
\t\tmessageChannel,
\t\tstreamParams,
\t\tonReasoningStream: (payload) => {
\t\t\tif (typeof payload.text === "string" && payload.text.length > 0) reasoningContent = payload.text;
\t\t},
\t\tonReasoningEnd: () => {}
\t});`,
      "gateway reasoning callback",
    );
  } else if (!text.includes("\t\tstreamParams,\n\t\tonReasoningStream: (payload) => {")) {
    text = replaceOnce(
      text,
      "\t\tmessageChannel,\n\t\tonReasoningStream: (payload) => {",
      "\t\tmessageChannel,\n\t\tstreamParams,\n\t\tonReasoningStream: (payload) => {",
      "chat completions command stream params",
    );
  }

  if (!text.includes("reasoning_content: reasoningContent || null")) {
    text = replaceOnce(
      text,
      "\t\t\t\t\tmessage: {\n\t\t\t\t\t\trole: \"assistant\",\n\t\t\t\t\t\tcontent\n\t\t\t\t\t},",
      "\t\t\t\t\tmessage: {\n\t\t\t\t\t\trole: \"assistant\",\n\t\t\t\t\t\tcontent,\n\t\t\t\t\t\treasoning_content: reasoningContent || null\n\t\t\t\t\t},",
      "non-stream reasoning response",
    );
  }

  if (!text.includes("let lastReasoningContent = \"\";")) {
    text = replaceOnce(
      text,
      "\tlet wroteRole = false;\n\tlet sawAssistantDelta = false;\n\tlet closed = false;",
      "\tlet wroteRole = false;\n\tlet sawAssistantDelta = false;\n\tlet lastReasoningContent = \"\";\n\tlet closed = false;",
      "stream reasoning state",
    );
  }

  if (!text.includes("if (evt.stream === \"thinking\")")) {
    text = replaceOnce(
      text,
      "\t\tif (evt.stream === \"lifecycle\") {",
      `\t\tif (evt.stream === "thinking") {
\t\t\tconst reasoningSnapshot = typeof evt.data?.text === "string" ? evt.data.text : void 0;
\t\t\tconst reasoningChunkRaw = resolveReasoningStreamDeltaText(evt);
\t\t\tconst reasoningChunk = reasoningSnapshot ? sliceLongestCommonPrefixDelta(lastReasoningContent, reasoningSnapshot) : reasoningChunkRaw;
\t\t\tif (!reasoningChunk) return;
\t\t\tif (reasoningSnapshot) lastReasoningContent = reasoningSnapshot;
\t\t\telse lastReasoningContent += reasoningChunk;
\t\t\tif (!wroteRole) {
\t\t\t\twroteRole = true;
\t\t\t\twriteAssistantRoleChunk(res, {
\t\t\t\t\trunId,
\t\t\t\t\tmodel
\t\t\t\t});
\t\t\t}
\t\t\twriteAssistantDeltaChunk(res, {
\t\t\t\trunId,
\t\t\t\tmodel,
\t\t\t\treasoningContent: reasoningChunk,
\t\t\t\tfinishReason: null
\t\t\t});
\t\t\treturn;
\t\t}
\t\tif (evt.stream === "lifecycle") {`,
      "stream thinking SSE forwarding",
    );
  }

  text = text.replaceAll("writeAssistantContentChunk(res, {", "writeAssistantDeltaChunk(res, {");

  write(path, text);
  console.log(`PATCHED gateway bundle: ${path}`);
}

const distDir = "/app/node_modules/openclaw/dist";
if (!fs.existsSync(distDir)) {
  throw new Error(`Missing OpenClaw dist directory: ${distDir}`);
}

const jsFiles = fs.readdirSync(distDir)
  .filter((name) => name.endsWith(".js"))
  .map((name) => `${distDir}/${name}`);

const commandBundles = jsFiles.filter((path) => {
  const text = read(path);
  return text.includes("function runAgentAttempt(params)")
    && text.includes("streamParams: params.opts.streamParams");
});
if (commandBundles.length === 0) {
  throw new Error("No command bundles found for reasoning passthrough patch");
}
for (const path of commandBundles) {
  patchReplyBundle(path);
}

const gatewayBundles = jsFiles.filter((path) => {
  const text = read(path);
  return path.includes("/gateway-cli-") && text.includes("function buildAgentCommandInput");
});
if (gatewayBundles.length === 0) {
  throw new Error("No gateway bundles found for reasoning SSE patch");
}
for (const path of gatewayBundles) {
  patchGatewayBundle(path);
}

console.log("Podman OpenClaw reasoning stream patch done.");
