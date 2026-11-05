import AppKit
import SwiftUI

/// Settings for the "BYO Python" build.  The app does not bundle a runtime in
/// that variant; the user picks an interpreter that already has the Python
/// dependencies, or installs them with the copied pip command.
struct PythonSetupView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var path: String

    init(model: AppModel) {
        self.model = model
        _path = State(initialValue: model.pythonPath)
    }

    private var requirementsPath: String {
        CoreClient.externalRequirements.path
    }

    private var installCommand: String {
        let python = path.trimmingCharacters(in: .whitespacesAndNewlines)
        let executable = python.isEmpty ? "python3" : python
        return "\"\(executable)\" -m pip install -r \"\(requirementsPath)\""
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 9) {
                Image(systemName: "terminal")
                    .font(.system(size: 18))
                    .foregroundStyle(.tint)
                Text("Python 环境")
                    .font(.title2.weight(.semibold))
                Spacer()
            }

            Text("这个版本不打包 Python 运行时。请选择一个已安装 numpy、opencv-python、Pillow 的 Python 3.10+；也可以先在目标环境里安装依赖，再选中它。")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                TextField("/path/to/python3", text: $path)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                Button("选择…") { pickPython() }
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("如果依赖还没装，复制下面命令到终端执行（会修改该 Python 环境）：")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text(installCommand)
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                HStack(spacing: 14) {
                    Button("复制 pip 命令") {
                        let pasteboard = NSPasteboard.general
                        pasteboard.clearContents()
                        pasteboard.setString(installCommand, forType: .string)
                    }
                    Button("显示 requirements.txt") { model.revealPythonRequirements() }
                }
                .buttonStyle(.link)
                .font(.system(size: 11))
            }

            if let message = model.errorMessage {
                Text(message)
                    .font(.system(size: 11))
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            HStack {
                Button("取消") { dismiss() }
                Spacer()
                Button("保存并重启") {
                    model.setPythonPath(path)
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 640, height: 380)
    }

    private func pickPython() {
        let panel = NSOpenPanel()
        panel.title = "选择 Python 解释器"
        panel.prompt = "选择"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        panel.message = "请选择已安装 numpy、opencv-python、Pillow 的 Python 3.10+ 可执行文件。"
        if panel.runModal() == .OK, let url = panel.url {
            path = url.path
        }
    }
}
