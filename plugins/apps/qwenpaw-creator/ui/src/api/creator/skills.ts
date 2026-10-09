import { creatorRequest, jsonBody } from "./client";

export interface SkillItem {
  name: string;
  description: string | null;
  enabled: boolean;
  status: "available" | "unavailable";
  reason: string | null;
  builtin: boolean;
}

export interface SkillContent {
  ok: boolean;
  skill: string;
  content: string;
  truncated: boolean;
}

export function listSkills(): Promise<{ items: SkillItem[] }> {
  return creatorRequest<{ items: SkillItem[] }>("/skills");
}

export function getSkillContent(name: string): Promise<SkillContent> {
  return creatorRequest<SkillContent>(
    `/skills/${encodeURIComponent(name)}/content`,
  );
}

export function saveSkill(
  name: string,
  content: string,
): Promise<{ ok: boolean; name: string }> {
  return creatorRequest<{ ok: boolean; name: string }>("/skills", {
    method: "POST",
    body: jsonBody({ name, content }),
  });
}

export function setSkillEnabled(
  name: string,
  enabled: boolean,
): Promise<{ ok: boolean; name: string; enabled: boolean }> {
  return creatorRequest<{ ok: boolean; name: string; enabled: boolean }>(
    `/skills/${encodeURIComponent(name)}`,
    { method: "PATCH", body: jsonBody({ enabled }) },
  );
}

export function deleteSkill(name: string): Promise<{ deleted: string }> {
  return creatorRequest<{ deleted: string }>(
    `/skills/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
}

export interface SkillImportResult {
  imported: string[];
  skipped: Array<{ name: string; reason: string }>;
  count: number;
}

export function uploadSkillZip(file: File): Promise<SkillImportResult> {
  const form = new FormData();
  form.append("file", file);
  return creatorRequest<SkillImportResult>("/skills/upload", {
    method: "POST",
    body: form,
  });
}
