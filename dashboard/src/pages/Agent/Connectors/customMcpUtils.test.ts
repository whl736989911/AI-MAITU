import { describe, expect, it } from "vitest";

import {
  cardsToServers,
  oauthClientFromCard,
  serversToCards,
} from "./customMcpUtils";

describe("custom MCP OAuth client editor", () => {
  it("serializes public OAuth app fields without echoing a saved secret", () => {
    const [card] = serversToCards({
      linear: {
        transport: "streamable_http",
        url: "https://mcp.example.com/mcp",
        oauth_client: {
          client_id: "client-id",
          has_client_secret: true,
          scope: "read write",
        },
      },
    });

    expect(card.oauthClientSecret).toBe("");
    expect(card.oauthClientSecretSaved).toBe(true);
    expect(cardsToServers([card]).linear.oauth_client).toEqual({
      client_id: "client-id",
      scope: "read write",
    });
  });

  it("includes a newly typed secret and omits an empty client id", () => {
    const [card] = serversToCards({
      linear: {
        transport: "streamable_http",
        url: "https://mcp.example.com/mcp",
      },
    });
    const withClient = {
      ...card,
      oauthClientId: "client-id",
      oauthClientSecret: "secret",
      oauthAuthorizationEndpoint: "https://auth.example.com/authorize",
      oauthTokenEndpoint: "https://auth.example.com/token",
    };
    expect(oauthClientFromCard(withClient)).toEqual({
      client_id: "client-id",
      client_secret: "secret",
      authorization_endpoint: "https://auth.example.com/authorize",
      token_endpoint: "https://auth.example.com/token",
    });
    expect(oauthClientFromCard({ ...card, oauthClientSecret: "secret" })).toBe(
      undefined,
    );
  });
});
