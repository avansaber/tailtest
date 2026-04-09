import Anthropic from "@anthropic-ai/sdk";

const SYSTEM_PROMPT = `You are a helpful coding assistant. You write clean
TypeScript code following the project's conventions. When the user asks for
changes, you propose concrete diffs and explain your reasoning.`;

export async function runAgent(userMessage: string): Promise<string> {
  const client = new Anthropic();
  const response = await client.messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 1024,
    system: SYSTEM_PROMPT,
    messages: [{ role: "user", content: userMessage }],
  });
  return (response.content[0] as { type: "text"; text: string }).text;
}
