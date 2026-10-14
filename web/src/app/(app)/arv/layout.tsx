"use client";

/* Ant Design for the ARV screens only. The tokens repeat MangoTree's palette
   (globals.css) so antd components look native here: mango-leaf accent, warm
   neutral surfaces, 14px radius, Geist. Dark mode follows the app's `.dark`
   class on <html>. Nothing outside /arv is touched. */
import * as React from "react";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { App, ConfigProvider, theme as antTheme } from "antd";
import "./arv.css";

const LIGHT = {
  colorPrimary: "#1f6f5f", colorSuccess: "#15803d", colorWarning: "#b45309", colorError: "#c2410c", colorInfo: "#475569", colorLink: "#1f6f5f",
  colorText: "#16161a", colorTextSecondary: "#6b6b76", colorTextTertiary: "#9a9aa6", colorTextQuaternary: "#b5b5bf",
  colorBorder: "#e6e3dc", colorBorderSecondary: "#e6e3dc", colorSplit: "#e6e3dc",
  colorBgContainer: "#ffffff", colorBgElevated: "#ffffff", colorBgLayout: "#f7f6f3", colorBgSpotlight: "#16161a",
  colorFillAlter: "#f7f6f3", colorFillSecondary: "#efede8", colorFillTertiary: "#f3f1ec", colorFillQuaternary: "#f7f6f3",
  colorPrimaryBg: "#e3f1ec", colorPrimaryBgHover: "#d6ebe3", colorSuccessBg: "#e9f7ee", colorWarningBg: "#fff7e6", colorErrorBg: "#fff1ea", colorInfoBg: "#f1f4f8",
};
const DARK = {
  colorPrimary: "#5fc2a9", colorSuccess: "#4ade80", colorWarning: "#f59e0b", colorError: "#fb923c", colorInfo: "#94a3b8", colorLink: "#5fc2a9",
  colorText: "#ecebe7", colorTextSecondary: "#a1a1ab", colorTextTertiary: "#6e6e78", colorTextQuaternary: "#55555e",
  colorBorder: "#24262c", colorBorderSecondary: "#24262c", colorSplit: "#24262c",
  colorBgContainer: "#17181c", colorBgElevated: "#1d1e23", colorBgLayout: "#0f1013", colorBgSpotlight: "#ecebe7",
  colorFillAlter: "#0b0c0e", colorFillSecondary: "#0b0c0e", colorFillTertiary: "#121317", colorFillQuaternary: "#0f1013",
  colorPrimaryBg: "#14312a", colorPrimaryBgHover: "#1a3d34", colorSuccessBg: "#0f2a1a", colorWarningBg: "#3a2a0c", colorErrorBg: "#3a1c0e", colorInfoBg: "#1a1f28",
};

function useIsDark() {
  const [dark, setDark] = React.useState(false);
  React.useEffect(() => {
    const el = document.documentElement;
    const read = () => setDark(el.classList.contains("dark"));
    read();
    const obs = new MutationObserver(read);
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

export default function ArvLayout({ children }: { children: React.ReactNode }) {
  const dark = useIsDark();
  const palette = dark ? DARK : LIGHT;
  return (
    <AntdRegistry>
      <ConfigProvider
        theme={{
          cssVar: { key: "arv" },
          hashed: false,
          algorithm: dark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
          token: {
            ...palette,
            fontFamily: "var(--font-sans)", fontSize: 13, fontSizeSM: 12, fontSizeLG: 14, fontSizeHeading4: 17, fontSizeHeading5: 14,
            borderRadius: 10, borderRadiusLG: 14, borderRadiusSM: 8, borderRadiusXS: 6,
            controlHeight: 36, controlHeightSM: 30, controlHeightLG: 44, lineWidth: 1,
            boxShadow: "0 8px 30px rgba(20, 20, 30, 0.08)", boxShadowSecondary: "0 8px 30px rgba(20, 20, 30, 0.10)", boxShadowTertiary: "0 1px 2px rgba(20, 20, 30, 0.05)",
            motionDurationMid: "0.15s",
          },
          components: {
            Card: { paddingLG: 20, headerFontSize: 13, headerHeight: 46, headerHeightSM: 40, bodyPadding: 20, headerPadding: 20, extraColor: palette.colorTextSecondary },
            Table: { headerBg: palette.colorFillSecondary, headerColor: palette.colorTextSecondary, headerSplitColor: "transparent", cellPaddingBlock: 10, cellPaddingInline: 12, cellPaddingBlockSM: 6, cellPaddingInlineSM: 8, cellFontSize: 12.5, cellFontSizeSM: 12, rowHoverBg: palette.colorFillQuaternary, borderColor: palette.colorBorderSecondary, headerBorderRadius: 0 },
            Tabs: { horizontalItemPadding: "10px 4px", horizontalItemGutter: 20, titleFontSize: 13, inkBarColor: palette.colorPrimary, itemColor: palette.colorTextSecondary, itemHoverColor: palette.colorText },
            Statistic: { titleFontSize: 11, contentFontSize: 22 },
            Steps: { iconSize: 26, titleLineHeight: 26, fontSizeIcon: 12 },
            Collapse: { headerBg: "transparent", headerPadding: "10px 16px", contentPadding: "4px 16px 16px", contentBg: "transparent", borderRadiusLG: 14 },
            Segmented: { trackBg: palette.colorFillSecondary, itemSelectedBg: palette.colorBgContainer, itemSelectedColor: palette.colorText, itemColor: palette.colorTextSecondary, trackPadding: 2, borderRadius: 9, borderRadiusSM: 8 },
            Tag: { fontSize: 11, fontSizeSM: 11, lineHeightSM: 1.6, borderRadiusSM: 999 },
            Button: { fontWeight: 500, contentFontSize: 13, contentFontSizeSM: 12, paddingInline: 14, borderRadius: 10, borderRadiusSM: 8, primaryShadow: "0 1px 2px rgba(20,20,30,0.08)", defaultShadow: "none" },
            Input: { paddingInline: 12 }, InputNumber: { paddingInline: 12, controlWidth: 0 }, Select: { optionSelectedBg: palette.colorPrimaryBg },
            Descriptions: { itemPaddingBottom: 6, labelBg: palette.colorFillQuaternary, titleMarginBottom: 8 },
            Progress: { defaultColor: palette.colorPrimary, remainingColor: palette.colorFillSecondary },
            Alert: { withDescriptionPadding: "12px 16px" },
            Modal: { titleFontSize: 16 },
            Upload: { colorFillAlter: palette.colorFillQuaternary },
          },
        }}
      >
        <App component={false}>{children}</App>
      </ConfigProvider>
    </AntdRegistry>
  );
}
