export interface DemoNotification {
  id: string;
  title: string;
  body: string;
  read: boolean;
  path: string;
  tab?: string;
}
export const DEMO_NOTIFICATION_KEY = "full.notifications";
export function initialDemoNotifications(
  t: (key: string) => string,
): DemoNotification[] {
  return [
    {
      id: "notice-knowledge",
      title: t("interfacePreview.full.common.sampleNotification"),
      body: t("interfacePreview.full.common.sampleNotificationBody"),
      read: false,
      path: "/knowledge-bases",
      tab: "permissions",
    },
  ];
}
