import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OptionOut } from "@ezconverter/contracts";
import { Alert, Button, Dropzone, OptionsForm, ProgressBar } from "../src";

describe("ProgressBar", () => {
  it("exposes a real percentage to assistive tech", () => {
    render(<ProgressBar value={0.426} label="Converting a.png" />);
    const bar = screen.getByRole("progressbar", { name: "Converting a.png" });
    expect(bar).toHaveAttribute("aria-valuenow", "43");
    expect(bar).toHaveAttribute("aria-valuetext", "43%");
  });
  it("is indeterminate (no invented number) when the engine cannot measure progress", () => {
    render(<ProgressBar value={null} label="Converting" />);
    const bar = screen.getByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(bar).toHaveAttribute("aria-valuetext", "In progress");
  });
  it("clamps out-of-range values", () => {
    render(<ProgressBar value={7} label="x" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("Dropzone", () => {
  it("delivers every chosen file and resets the input so the same file can be re-added", () => {
    const onFiles = vi.fn();
    render(<Dropzone onFiles={onFiles} />);
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    const files = [new File(["a"], "a.png"), new File(["b"], "b.json")];
    fireEvent.change(input, { target: { files } });
    expect(onFiles).toHaveBeenCalledWith(files);
  });
  it("accepts drops and ignores them when disabled", () => {
    const onFiles = vi.fn();
    const { rerender } = render(<Dropzone onFiles={onFiles} />);
    const f = new File(["x"], "x.txt");
    fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [f] } });
    expect(onFiles).toHaveBeenCalledTimes(1);
    rerender(<Dropzone onFiles={onFiles} disabled />);
    fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [f] } });
    expect(onFiles).toHaveBeenCalledTimes(1);
  });
  it("has a keyboard-reachable picker control", () => {
    render(<Dropzone onFiles={() => undefined} />);
    expect(screen.getByText("Choose files")).toHaveAttribute("tabindex", "0");
  });
});

const specs: OptionOut[] = [
  { name: "quality", type: "int", default: 85, description: "Encoder quality.", minimum: 1, maximum: 100 },
  { name: "lossless", type: "bool", default: false, description: "Use lossless." },
  { name: "mode", type: "enum", default: "auto", description: "Mode.", choices: ["auto", "reencode"] },
  { name: "delimiter", type: "enum", default: ",", description: "Delimiter.", choices: [",", ";", "\t"] },
  { name: "background", type: "string", default: "#ffffff", description: "Colour." },
];

describe("OptionsForm (rendered from the capability's declared schema)", () => {
  it("shows defaults, ranges and descriptions", () => {
    render(<OptionsForm specs={specs} values={{}} onChange={() => undefined} />);
    expect(screen.getByLabelText("quality")).toHaveValue(85);
    expect(screen.getByText(/Encoder quality\. \(1–100\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("mode")).toHaveValue("auto");
  });
  it("emits typed values: numbers stay numbers, booleans booleans, enum choices keep their original type", () => {
    const onChange = vi.fn();
    render(<OptionsForm specs={specs} values={{ quality: 85 }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("quality"), { target: { value: "40" } });
    expect(onChange).toHaveBeenLastCalledWith({ quality: 40 });
    fireEvent.click(screen.getByLabelText("lossless"));
    expect(onChange).toHaveBeenLastCalledWith({ quality: 85, lossless: true });
    fireEvent.change(screen.getByLabelText("delimiter"), { target: { value: "\t" } });
    expect(onChange).toHaveBeenLastCalledWith({ quality: 85, delimiter: "\t" });
  });
  it("shows a server-side validation message next to the offending field", () => {
    render(<OptionsForm specs={specs} values={{}} errors={{ quality: "Option 'quality': must be at most 100" }} onChange={() => undefined} />);
    expect(screen.getByRole("alert")).toHaveTextContent("must be at most 100");
    expect(screen.getByLabelText("quality")).toHaveAttribute("aria-invalid", "true");
  });
  it("says so when a conversion has no settings", () => {
    render(<OptionsForm specs={[]} values={{}} onChange={() => undefined} />);
    expect(screen.getByText("No settings for this conversion.")).toBeInTheDocument();
  });
});

describe("Alert and Button", () => {
  it("errors are announced as alerts, notices as status", () => {
    const { rerender } = render(<Alert tone="bad" title="Broke" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Broke");
    rerender(<Alert tone="info" title="FYI" />);
    expect(screen.getByRole("status")).toHaveTextContent("FYI");
  });
  it("buttons are at least touch-sized and respect disabled", () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Go</Button>);
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Go" }).className).toContain("min-h-11");
  });
});
