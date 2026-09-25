import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Divider,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Typography,
} from "antd";
import { CheckCircle2, Images, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  mediaGenerationApi,
  type MediaGenerationSettings,
  type MediaProviderInput,
  type MediaProviderName,
  type MediaProviderPreset,
} from "../../../api/modules/mediaGeneration";
import { message } from "@/utils/antdMessage";
import { TabPanelHeader } from "../AdvancedSettings/TabPanelHeader";
import tabStyles from "../AdvancedSettings/tabContent.module.less";

const { Text } = Typography;
type BaseUrlError = "invalid" | "https" | "host" | "query";

function getBaseUrlError(
  baseUrl: string,
  officialBaseUrl: string,
): BaseUrlError | null {
  let url: URL;
  let officialUrl: URL;
  try {
    url = new URL(baseUrl);
    officialUrl = new URL(officialBaseUrl);
  } catch {
    return "invalid";
  }
  if (url.protocol !== "https:") return "https";
  if (url.search || url.hash) return "query";
  if (
    url.hostname !== officialUrl.hostname ||
    url.port ||
    url.username ||
    url.password
  ) {
    return "host";
  }
  return null;
}

function getOfficialProviderBaseUrl(
  provider: MediaProviderName,
  presets: MediaProviderPreset[],
): string | null {
  return (
    presets.find((preset) => preset.provider === provider)?.base_url ?? null
  );
}

function getOfficialProviderHost(
  provider: MediaProviderName,
  presets: MediaProviderPreset[],
): string | null {
  const baseUrl = getOfficialProviderBaseUrl(provider, presets);
  if (!baseUrl) return null;
  try {
    return new URL(baseUrl).hostname;
  } catch {
    return null;
  }
}

function getProviderBaseUrlError(
  provider: ProviderDraft,
  presets: MediaProviderPreset[],
): BaseUrlError | null {
  const officialBaseUrl = getOfficialProviderBaseUrl(
    provider.provider,
    presets,
  );
  return officialBaseUrl
    ? getBaseUrlError(provider.base_url, officialBaseUrl)
    : null;
}

type ProviderDraft = MediaProviderInput & {
  api_key_set: boolean;
  apiKey: string;
};

const toDraft = (
  provider: MediaGenerationSettings["providers"][number],
): ProviderDraft => ({
  id: provider.id,
  provider: provider.provider,
  display_name: provider.display_name,
  enabled: provider.enabled,
  base_url: provider.base_url,
  image_enabled: provider.image_enabled,
  video_enabled: provider.video_enabled,
  image_model: provider.image_model,
  video_model: provider.video_model,
  api_key_set: provider.api_key_set,
  apiKey: "",
});

export function MediaGenerationSettingsPanel() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<MediaGenerationSettings | null>(
    null,
  );
  const [providers, setProviders] = useState<ProviderDraft[]>([]);
  const [presets, setPresets] = useState<MediaProviderPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [addingProvider, setAddingProvider] = useState<MediaProviderName>();

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    try {
      const [config, presetList] = await Promise.all([
        mediaGenerationApi.get(),
        mediaGenerationApi.getPresets(),
      ]);
      setSettings(config);
      setProviders(config.providers.map(toDraft));
      setPresets(presetList);
    } catch (err) {
      message.error(t("mediaGeneration.loadError"));
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchConfig();
  }, [fetchConfig]);

  const updateProvider = (id: string, patch: Partial<ProviderDraft>) => {
    setProviders((current) =>
      current.map((provider) =>
        provider.id === id ? { ...provider, ...patch } : provider,
      ),
    );
  };

  const addProvider = () => {
    if (!addingProvider) return;
    const preset = presets.find((item) => item.provider === addingProvider);
    if (!preset) return;
    let suffix =
      providers.filter((item) => item.provider === preset.provider).length + 1;
    while (providers.some((item) => item.id === `${preset.provider}-${suffix}`))
      suffix += 1;
    const id = `${preset.provider}-${suffix}`;
    const next: ProviderDraft = {
      id,
      provider: preset.provider,
      display_name: preset.display_name,
      enabled: true,
      base_url: preset.base_url,
      image_enabled: true,
      video_enabled: true,
      image_model: preset.image_models[0] ?? "",
      video_model: preset.video_models[0] ?? "",
      api_key_set: false,
      apiKey: "",
    };
    setProviders((current) => [...current, next]);
    setAddingProvider(undefined);
  };

  const routeOptions = (kind: "image" | "video") => [
    { value: "", label: t("mediaGeneration.noRoute") },
    ...providers
      .filter(
        (provider) =>
          provider.enabled &&
          provider[`${kind}_enabled`] &&
          (provider.api_key_set || Boolean(provider.apiKey.trim())),
      )
      .map((provider) => ({
        value: provider.id,
        label: provider.display_name,
      })),
  ];

  const payloadFor = (provider: ProviderDraft): MediaProviderInput => {
    const apiKey = provider.apiKey.trim();
    return {
      id: provider.id,
      provider: provider.provider,
      display_name: provider.display_name,
      enabled: provider.enabled,
      base_url: provider.base_url,
      image_enabled: provider.image_enabled,
      video_enabled: provider.video_enabled,
      image_model: provider.image_model,
      video_model: provider.video_model,
      ...(apiKey ? { api_key: apiKey } : {}),
    };
  };

  const handleSave = async () => {
    if (!settings) return;
    if (
      providers.some(
        (provider) => getProviderBaseUrlError(provider, presets) !== null,
      )
    ) {
      message.error(t("mediaGeneration.baseUrlValidationError"));
      return;
    }
    try {
      setSaving(true);
      const saved = await mediaGenerationApi.save({
        enabled: settings.enabled,
        providers: providers.map(payloadFor),
        default_image_provider: settings.default_image_provider,
        default_video_provider: settings.default_video_provider,
      });
      setSettings(saved);
      setProviders(saved.providers.map(toDraft));
      message.success(t("mediaGeneration.saved"));
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("mediaGeneration.saveFailed"),
      );
    } finally {
      setSaving(false);
    }
  };

  const confirmClearKey = (provider: ProviderDraft) => {
    Modal.confirm({
      title: t("mediaGeneration.clearApiKeyConfirmTitle"),
      content: t("mediaGeneration.clearApiKeyConfirmBody", {
        name: provider.display_name,
      }),
      okText: t("mediaGeneration.clearApiKey"),
      cancelText: t("common.cancel"),
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          setSaving(true);
          const cleared = providers.map((item) =>
            item.id === provider.id
              ? { ...payloadFor(item), api_key: undefined, clear_api_key: true }
              : payloadFor(item),
          );
          const saved = await mediaGenerationApi.save({
            enabled: settings?.enabled ?? false,
            providers: cleared,
            default_image_provider: settings?.default_image_provider ?? null,
            default_video_provider: settings?.default_video_provider ?? null,
          });
          setSettings(saved);
          setProviders(saved.providers.map(toDraft));
          message.success(t("mediaGeneration.clearApiKeySuccess"));
        } catch (err) {
          message.error(
            err instanceof Error
              ? err.message
              : t("mediaGeneration.saveFailed"),
          );
          throw err;
        } finally {
          setSaving(false);
        }
      },
    });
  };

  const testProvider = async (
    provider: ProviderDraft,
    kind: "credentials" | "image" | "video",
  ) => {
    const key = `${provider.id}:${kind}`;
    try {
      setTesting(key);
      const result = await mediaGenerationApi.test({
        kind,
        provider: payloadFor(provider),
      });
      if (result.ok) {
        message.success(
          t(
            kind === "credentials"
              ? "mediaGeneration.testSuccess"
              : kind === "image"
              ? "mediaGeneration.imageTestSuccess"
              : "mediaGeneration.videoTestSuccess",
          ),
        );
      } else {
        message.error(result.error || t("mediaGeneration.testFailed"));
      }
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("mediaGeneration.testFailed"),
      );
    } finally {
      setTesting(null);
    }
  };

  const modelOptions = (provider: ProviderDraft, kind: "image" | "video") => {
    const preset = presets.find((item) => item.provider === provider.provider);
    const values =
      kind === "image"
        ? preset?.image_models ?? []
        : preset?.video_models ?? [];
    return values.map((value) => ({ value, label: value }));
  };

  return (
    <>
      <TabPanelHeader
        icon={<Images size={22} />}
        title={t("mediaGeneration.title")}
        description={t("mediaGeneration.description")}
      />
      {loading ? (
        <Text type="secondary">{t("mediaGeneration.loading")}</Text>
      ) : settings ? (
        <div className={tabStyles.formFields}>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={t("mediaGeneration.hint")}
          />
          <div style={{ marginBottom: 20 }}>
            <Text strong>{t("mediaGeneration.enable")}</Text>
            <div style={{ marginTop: 8 }}>
              <Switch
                checked={settings.enabled}
                onChange={(enabled) => setSettings({ ...settings, enabled })}
              />
            </div>
          </div>
          <Divider orientation="left">
            {t("mediaGeneration.providerListTitle")}
          </Divider>
          <Space wrap style={{ marginBottom: 16 }}>
            <Select
              aria-label={t("mediaGeneration.providerPreset")}
              placeholder={t("mediaGeneration.providerPreset")}
              value={addingProvider}
              onChange={setAddingProvider}
              style={{ minWidth: 240 }}
              options={presets.map((preset) => ({
                value: preset.provider,
                label: preset.display_name,
              }))}
            />
            <Button
              icon={<Plus size={14} />}
              disabled={!addingProvider}
              onClick={addProvider}
            >
              {t("mediaGeneration.addProvider")}
            </Button>
          </Space>
          {providers.map((provider) => {
            const officialHost = getOfficialProviderHost(
              provider.provider,
              presets,
            );
            const baseUrlError = getProviderBaseUrlError(provider, presets);
            return (
              <Card
                key={provider.id}
                size="small"
                title={provider.display_name}
                style={{ marginBottom: 16 }}
                extra={
                  <Space>
                    <Text type="secondary">{provider.id}</Text>
                    <Button
                      danger
                      type="text"
                      aria-label={t("mediaGeneration.removeProvider", {
                        name: provider.display_name,
                      })}
                      icon={<Trash2 size={14} />}
                      onClick={() =>
                        Modal.confirm({
                          title: t(
                            "mediaGeneration.removeProviderConfirmTitle",
                          ),
                          content: t(
                            "mediaGeneration.removeProviderConfirmBody",
                            {
                              name: provider.display_name,
                            },
                          ),
                          okButtonProps: { danger: true },
                          onOk: () => {
                            setProviders((current) =>
                              current.filter((item) => item.id !== provider.id),
                            );
                            setSettings((current) =>
                              current
                                ? {
                                    ...current,
                                    default_image_provider:
                                      current.default_image_provider ===
                                      provider.id
                                        ? null
                                        : current.default_image_provider,
                                    default_video_provider:
                                      current.default_video_provider ===
                                      provider.id
                                        ? null
                                        : current.default_video_provider,
                                  }
                                : current,
                            );
                          },
                        })
                      }
                    />
                  </Space>
                }
              >
                <Space
                  direction="vertical"
                  style={{ width: "100%" }}
                  size="middle"
                >
                  <Space wrap align="start" style={{ width: "100%" }}>
                    <label>
                      <Text type="secondary">
                        {t("mediaGeneration.enabled")}
                      </Text>
                      <div style={{ marginTop: 6 }}>
                        <Switch
                          checked={provider.enabled}
                          onChange={(enabled) =>
                            updateProvider(provider.id, { enabled })
                          }
                        />
                      </div>
                    </label>
                    <label style={{ flex: 1, minWidth: 200 }}>
                      <Text type="secondary">
                        {t("mediaGeneration.providerName")}
                      </Text>
                      <Input
                        value={provider.display_name}
                        onChange={(event) =>
                          updateProvider(provider.id, {
                            display_name: event.target.value,
                          })
                        }
                      />
                    </label>
                    <label style={{ flex: 2, minWidth: 260 }}>
                      <Text type="secondary">
                        {t("mediaGeneration.baseUrl")}
                      </Text>
                      <Input
                        status={baseUrlError ? "error" : undefined}
                        value={provider.base_url}
                        onChange={(event) =>
                          updateProvider(provider.id, {
                            base_url: event.target.value,
                          })
                        }
                      />
                      <Text type={baseUrlError ? "danger" : "secondary"}>
                        {baseUrlError
                          ? t(`mediaGeneration.baseUrlError.${baseUrlError}`, {
                              host: officialHost,
                            })
                          : officialHost
                          ? t("mediaGeneration.baseUrlOfficialHostHelp", {
                              host: officialHost,
                            })
                          : null}
                      </Text>
                    </label>
                  </Space>
                  <div>
                    <Text type="secondary">{t("mediaGeneration.apiKey")}</Text>
                    <Input.Password
                      value={provider.apiKey}
                      autoComplete="new-password"
                      placeholder={
                        provider.api_key_set
                          ? t("mediaGeneration.apiKeySet")
                          : t("mediaGeneration.apiKeyRequired")
                      }
                      onChange={(event) =>
                        updateProvider(provider.id, {
                          apiKey: event.target.value,
                        })
                      }
                    />
                    {provider.api_key_set && (
                      <Text type="secondary">
                        <CheckCircle2 size={12} style={{ marginRight: 4 }} />
                        {t("mediaGeneration.apiKeySet")}
                      </Text>
                    )}
                  </div>
                  <Space wrap>
                    <Button
                      loading={testing === `${provider.id}:credentials`}
                      disabled={
                        !provider.api_key_set && !provider.apiKey.trim()
                      }
                      onClick={() => void testProvider(provider, "credentials")}
                    >
                      {t("mediaGeneration.testCredentials")}
                    </Button>
                    {provider.api_key_set && (
                      <Button danger onClick={() => confirmClearKey(provider)}>
                        {t("mediaGeneration.clearApiKey")}
                      </Button>
                    )}
                  </Space>
                  <Divider style={{ margin: "4px 0" }} />
                  <Space wrap align="start" style={{ width: "100%" }}>
                    <label>
                      <Text type="secondary">
                        {t("mediaGeneration.imageEnabled")}
                      </Text>
                      <div style={{ marginTop: 6 }}>
                        <Switch
                          checked={provider.image_enabled}
                          onChange={(image_enabled) =>
                            updateProvider(provider.id, { image_enabled })
                          }
                        />
                      </div>
                    </label>
                    <label style={{ flex: 1, minWidth: 220 }}>
                      <Text type="secondary">
                        {t("mediaGeneration.imageModel")}
                      </Text>
                      <AutoComplete
                        style={{ width: "100%" }}
                        value={provider.image_model}
                        options={modelOptions(provider, "image")}
                        placeholder={t(
                          "mediaGeneration.customModelPlaceholder",
                        )}
                        onChange={(image_model) =>
                          updateProvider(provider.id, { image_model })
                        }
                      />
                    </label>
                    <Button
                      style={{ marginTop: 22 }}
                      loading={testing === `${provider.id}:image`}
                      disabled={
                        !provider.api_key_set && !provider.apiKey.trim()
                      }
                      onClick={() => void testProvider(provider, "image")}
                    >
                      {t("mediaGeneration.testImageModel")}
                    </Button>
                  </Space>
                  <Space wrap align="start" style={{ width: "100%" }}>
                    <label>
                      <Text type="secondary">
                        {t("mediaGeneration.videoEnabled")}
                      </Text>
                      <div style={{ marginTop: 6 }}>
                        <Switch
                          checked={provider.video_enabled}
                          onChange={(video_enabled) =>
                            updateProvider(provider.id, { video_enabled })
                          }
                        />
                      </div>
                    </label>
                    <label style={{ flex: 1, minWidth: 220 }}>
                      <Text type="secondary">
                        {t("mediaGeneration.videoModel")}
                      </Text>
                      <AutoComplete
                        style={{ width: "100%" }}
                        value={provider.video_model}
                        options={modelOptions(provider, "video")}
                        placeholder={t(
                          "mediaGeneration.customModelPlaceholder",
                        )}
                        onChange={(video_model) =>
                          updateProvider(provider.id, { video_model })
                        }
                      />
                    </label>
                    <Button
                      style={{ marginTop: 22 }}
                      loading={testing === `${provider.id}:video`}
                      disabled={
                        !provider.api_key_set && !provider.apiKey.trim()
                      }
                      onClick={() => void testProvider(provider, "video")}
                    >
                      {t("mediaGeneration.testVideoModel")}
                    </Button>
                  </Space>
                </Space>
              </Card>
            );
          })}
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message={t("mediaGeneration.modelTestBillingHint")}
          />
          <Divider orientation="left">
            {t("mediaGeneration.defaultRoutes")}
          </Divider>
          <Space wrap style={{ width: "100%", marginBottom: 24 }}>
            <label style={{ flex: 1, minWidth: 240 }}>
              <Text type="secondary">
                {t("mediaGeneration.imageDefaultRoute")}
              </Text>
              <Select
                style={{ width: "100%" }}
                value={settings.default_image_provider ?? ""}
                options={routeOptions("image")}
                onChange={(value) =>
                  setSettings({
                    ...settings,
                    default_image_provider: value || null,
                  })
                }
              />
            </label>
            <label style={{ flex: 1, minWidth: 240 }}>
              <Text type="secondary">
                {t("mediaGeneration.videoDefaultRoute")}
              </Text>
              <Select
                style={{ width: "100%" }}
                value={settings.default_video_provider ?? ""}
                options={routeOptions("video")}
                onChange={(value) =>
                  setSettings({
                    ...settings,
                    default_video_provider: value || null,
                  })
                }
              />
            </label>
          </Space>
          <Space>
            <Button
              type="primary"
              loading={saving}
              onClick={() => void handleSave()}
            >
              {t("common.save")}
            </Button>
            <Button
              icon={<RefreshCw size={14} />}
              onClick={() => void fetchConfig()}
            >
              {t("common.refresh")}
            </Button>
          </Space>
        </div>
      ) : (
        <Space>
          <Text type="secondary">{t("mediaGeneration.loadError")}</Text>
          <Button
            icon={<RefreshCw size={14} />}
            onClick={() => void fetchConfig()}
          >
            {t("common.refresh")}
          </Button>
        </Space>
      )}
    </>
  );
}

export default MediaGenerationSettingsPanel;
