import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigProvider } from "antd";
import SkillsConfigPane from "../SkillsConfigPane";
import type { SkillContent, SkillItem } from "@/api/creator/skills";
import zh from "@/locales/zh.json";

// Controllable API mocks so a test can hold skill "alpha"'s save open while
// the editor moves on to "beta" -- the state transition the save-completion
// sequence guard defends against (see the note inside that test on why the
// drive-through is synthetic rather than user-reachable).
const { listSkillsMock, getSkillContentMock, saveSkillMock } = vi.hoisted(
  () => ({
    listSkillsMock: vi.fn(),
    getSkillContentMock: vi.fn(),
    saveSkillMock: vi.fn(),
  }),
);

vi.mock("@/api/creator", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/creator")>();
  return {
    ...actual,
    listSkills: listSkillsMock,
    getSkillContent: getSkillContentMock,
    saveSkill: saveSkillMock,
  };
});

function skillItem(name: string): SkillItem {
  return {
    name,
    description: `${name} description`,
    enabled: true,
    status: "available",
    reason: null,
    builtin: false,
  };
}

function skillContent(name: string): SkillContent {
  return {
    ok: true,
    skill: name,
    content: `${name} original body`,
    truncated: false,
  };
}

/** motion:false makes the antd Modal mount/unmount synchronously under jsdom. */
function renderPane() {
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <SkillsConfigPane />
    </ConfigProvider>,
  );
}

// Row edit buttons in list order ([0]=alpha, [1]=beta). Scoped to role=button
// so the open editor dialog -- whose title is also t("skills.edit") and which
// is aria-labelledby that title -- is not matched.
function editButtons(): HTMLElement[] {
  return screen.getAllByRole("button", { name: zh.skills.edit });
}

// antd renders a two-CJK-character button label with a space ("保存" shows as
// "保 存"), so match the accessible name with optional whitespace per char.
function clickDialogButton(label: string): void {
  const name = new RegExp(label.split("").join("\\s*"));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name }),
  );
}

describe("SkillsConfigPane editor session guard", () => {
  beforeEach(() => {
    listSkillsMock.mockResolvedValue({
      items: [skillItem("alpha"), skillItem("beta")],
    });
    getSkillContentMock.mockImplementation((name: string) =>
      Promise.resolve(skillContent(name)),
    );
  });

  it("keeps a newer edit open when an older save resolves late", async () => {
    // alpha's save stays pending until the test releases it, so it resolves
    // only after the editor has moved on to beta.
    let releaseAlphaSave = () => {};
    saveSkillMock.mockImplementation((name: string) =>
      name === "alpha"
        ? new Promise<{ ok: boolean; name: string }>((resolve) => {
            releaseAlphaSave = () => resolve({ ok: true, name });
          })
        : Promise.resolve({ ok: true, name }),
    );

    renderPane();
    await screen.findByText("alpha");
    await screen.findByText("beta");
    expect(editButtons()).toHaveLength(2);

    // Open alpha and start a save that will not resolve yet.
    fireEvent.click(editButtons()[0]);
    await screen.findByDisplayValue("alpha original body");
    await act(async () => {
      clickDialogButton(zh.common.save);
    });
    expect(saveSkillMock).toHaveBeenCalledWith("alpha", "alpha original body");

    // While alpha's save is still in flight, move the editor to beta and make
    // an unsaved edit. antd 6.5.0 blocks every cancel/close path during
    // confirmLoading and its mask covers the row actions, so this synthetic
    // drive-through proves the session-sequence state machine only -- it is
    // not a reproduction of a user-reachable flow.
    fireEvent.click(editButtons()[1]);
    await screen.findByDisplayValue("beta original body");
    fireEvent.change(screen.getByDisplayValue("beta original body"), {
      target: { value: "beta UNSAVED edit" },
    });

    // alpha's save now resolves late, after the editor moved on to beta.
    await act(async () => {
      releaseAlphaSave();
    });

    // The guard: alpha's completion must not close beta or drop its edits. A
    // regression closes the dialog and it stays closed, so this never settles.
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeNull();
      expect(screen.getByDisplayValue("beta UNSAVED edit")).toBeVisible();
    });
  });

  it("closes the editor when the save is still the active session", async () => {
    saveSkillMock.mockResolvedValue({ ok: true, name: "alpha" });

    renderPane();
    await screen.findByText("alpha");
    fireEvent.click(editButtons()[0]);
    await screen.findByDisplayValue("alpha original body");

    await act(async () => {
      clickDialogButton(zh.common.save);
    });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(saveSkillMock).toHaveBeenCalledWith("alpha", "alpha original body");
  });
});
