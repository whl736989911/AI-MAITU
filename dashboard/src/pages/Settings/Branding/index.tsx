import { useEffect, useState } from "react";
import { Alert, Button, Card, Input, Space, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import { brandingApi, type Branding } from "../../../context/BrandingContext";
import styles from "./index.module.less";

const LOGO_SLOTS = [
  "mark",
  "wordmark_light",
  "wordmark_dark",
  "favicon",
  "apple_touch_icon",
  "pwa_192",
  "pwa_512",
] as const;
type LogoSlot = (typeof LOGO_SLOTS)[number];

export default function BrandingSettingsPage() {
  const { t, i18n } = useTranslation();
  const [brand, setBrand] = useState<Branding | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void brandingApi
      .get()
      .then(setBrand)
      .catch(() => setError(t("branding.loadFailed")));
  }, [t]);

  const editText = (
    group: "name" | "full_name" | "short_name" | "description",
    lang: "zh" | "en",
    value: string,
  ) => {
    setBrand((old) =>
      old ? { ...old, [group]: { ...old[group], [lang]: value } } : old,
    );
  };
  const editColor = (group: "colors" | "pwa", key: string, value: string) => {
    setBrand((old) =>
      old ? { ...old, [group]: { ...old[group], [key]: value } } : old,
    );
  };
  const upload = async (slot: LogoSlot, file?: File) => {
    if (!file || !brand) return;
    try {
      const result = await brandingApi.uploadLogo(slot, file);
      setBrand((old) =>
        old ? { ...old, logos: { ...old.logos, [slot]: result.url } } : old,
      );
      message.success(t("branding.logoUploaded"));
    } catch {
      message.error(t("branding.logoUploadFailed"));
    }
  };
  const save = async () => {
    if (!brand) return;
    setBusy(true);
    try {
      const result = await brandingApi.put(brand);
      setBrand(result);
      window.dispatchEvent(new Event("octop:branding-updated"));
      message.success(t("branding.saved"));
    } catch {
      message.error(t("branding.saveFailed"));
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    setBusy(true);
    try {
      const result = await brandingApi.reset();
      setBrand(result);
      window.dispatchEvent(new Event("octop:branding-updated"));
      message.success(t("branding.resetDone"));
    } catch {
      message.error(t("branding.resetFailed"));
    } finally {
      setBusy(false);
    }
  };

  if (!brand) return <div>{error || t("common.loading")}</div>;
  const language = i18n.language.toLowerCase().startsWith("zh") ? "zh" : "en";
  const colorFields = [
    ["colors", "brand", "brandColor"],
    ["colors", "accent", "accentColor"],
    ["pwa", "theme_color", "themeColor"],
    ["pwa", "background_color", "backgroundColor"],
  ] as const;

  return (
    <div className={styles.page}>
      <div className={styles.heading}>
        <Typography.Title level={3}>{t("branding.title")}</Typography.Title>
        <Typography.Paragraph>{t("branding.description")}</Typography.Paragraph>
      </div>
      <div className={styles.fields}>
        {(["name", "full_name", "short_name", "description"] as const).map(
          (group) => (
            <Card key={group} title={t(`branding.fields.${group}`)}>
              <div className={styles.localeGrid}>
                {(["zh", "en"] as const).map((lang) => (
                  <label key={lang} className={styles.field}>
                    <span>
                      {lang === "zh"
                        ? t("branding.chinese")
                        : t("branding.english")}
                    </span>
                    {group === "description" ? (
                      <Input.TextArea
                        rows={3}
                        value={brand[group][lang]}
                        onChange={(event) =>
                          editText(group, lang, event.target.value)
                        }
                      />
                    ) : (
                      <Input
                        value={brand[group][lang]}
                        onChange={(event) =>
                          editText(group, lang, event.target.value)
                        }
                      />
                    )}
                  </label>
                ))}
              </div>
            </Card>
          ),
        )}
        <Card title={t("branding.logos")}>
          <div className={styles.logoGrid}>
            {LOGO_SLOTS.map((slot) => (
              <label className={styles.logoCard} key={slot}>
                <span>{t(`branding.logoSlots.${slot}`)}</span>
                <img
                  src={brand.logos[slot]}
                  alt={t(`branding.logoSlots.${slot}`)}
                />
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/svg+xml,image/webp"
                  onChange={(event) =>
                    void upload(slot, event.target.files?.[0])
                  }
                />
              </label>
            ))}
          </div>
        </Card>
        <Card title={t("branding.colors")}>
          <div className={styles.colorGrid}>
            {colorFields.map(([group, key, label]) => {
              const value = (brand[group] as Record<string, string>)[key];
              return (
                <label className={styles.colorField} key={label}>
                  <span>{t(`branding.${label}`)}</span>
                  <input
                    type="color"
                    value={value}
                    onChange={(event) =>
                      editColor(group, key, event.target.value)
                    }
                  />
                  <Input
                    value={value}
                    onChange={(event) =>
                      editColor(group, key, event.target.value)
                    }
                  />
                </label>
              );
            })}
          </div>
        </Card>
      </div>
      <Card title={t("branding.preview")} className={styles.preview}>
        <div
          className={styles.previewHeader}
          style={{ background: brand.colors.brand }}
        >
          <img src={brand.logos.wordmark_light} alt="" />
          <strong>{brand.name[language]}</strong>
        </div>
        <div className={styles.previewBody}>
          <Typography.Title level={4}>
            {brand.full_name[language]}
          </Typography.Title>
          <Typography.Paragraph>
            {brand.description[language]}
          </Typography.Paragraph>
          <Button type="primary" style={{ background: brand.colors.accent }}>
            {t("branding.previewButton")}
          </Button>
        </div>
        <Alert type="info" showIcon message={t("branding.previewNote")} />
      </Card>
      <Space>
        <Button type="primary" loading={busy} onClick={() => void save()}>
          {t("branding.save")}
        </Button>
        <Button danger loading={busy} onClick={() => void reset()}>
          {t("branding.reset")}
        </Button>
      </Space>
    </div>
  );
}
