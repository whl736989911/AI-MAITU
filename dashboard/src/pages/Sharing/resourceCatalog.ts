import { request } from "../../api/request";
import { connectorsApi } from "../../api/modules/connectors";
import { knowledgeBasesApi } from "../../api/modules/knowledgeBases";
import type { SharingResourceType } from "../../api/modules/sharing";
import type { OctopAgent } from "../../context/AgentContext";

/**
 * The pickable resources of one type.
 *
 * Each catalog is owned by the module that already lists it for its own page;
 * this file only adapts the shapes to ``{id, name, hint}``. ``id`` is the key
 * the ACL endpoints address — ``agent_id`` / ``instance_id`` /
 * ``knowledge_base_id``, never a database row id.
 */
export interface SharingResourceOption {
  id: string;
  name: string;
  /** Second line: owner, kind, or description. ``null`` when unknown. */
  hint: string | null;
}

export async function loadSharingResources(
  resourceType: SharingResourceType,
): Promise<SharingResourceOption[]> {
  if (resourceType === "knowledge_base") {
    const bases = await knowledgeBasesApi.list();
    return bases.map((base) => ({
      id: base.knowledge_base_id ?? base.id,
      name: base.name,
      hint: base.owner_display_name ?? base.owner_username ?? null,
    }));
  }
  if (resourceType === "agent") {
    const agents = await request<OctopAgent[]>("/agents");
    return agents.map((agent) => ({
      id: agent.agent_id,
      name: agent.name,
      hint: agent.owner_username ?? null,
    }));
  }
  if (resourceType === "connector") {
    const instances = await connectorsApi.listInstances();
    return instances.map((instance) => ({
      id: instance.instance_id,
      name: instance.display_name,
      hint: instance.kind,
    }));
  }
  // Every type the picker offers is listed above; a stored row of a kind it no
  // longer offers has no catalog to build from. Refused as itself rather than
  // answered with an empty list, which would read as "nothing of that kind".
  throw new Error(
    `No resource catalog for sharing resource type "${resourceType}"`,
  );
}
