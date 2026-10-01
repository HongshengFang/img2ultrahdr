import AppKit

/// Scope menu changes to this photo editor's Edit menu. Clipboard items retain
/// their native responder-chain targets and automatic validation.
final class EditorMenuPolicy {
    static let shared = EditorMenuPolicy()
    private var observers: [NSObjectProtocol] = []
    private var applying = false
    func install() {
        guard observers.isEmpty else { return }
        for name in [NSMenu.didAddItemNotification, NSMenu.didBeginTrackingNotification] {
            observers.append(NotificationCenter.default.addObserver(forName:name, object:nil, queue:.main) { [weak self] _ in
                self?.apply()
            })
        }
        apply()
    }
    func apply(to edit: NSMenu) {
        guard !applying else { return }; applying = true
        defer { applying = false }
        if #available(macOS 15.2, *) { edit.automaticallyInsertsWritingToolsItems = false }
        let names: Set<String> = ["Undo", "Redo", "撤销", "重做", "Cut", "Copy", "Paste", "剪切", "拷贝", "复制", "粘贴"]
        let clipboard: Set<String> = ["cut:", "copy:", "paste:"]
        for item in edit.items where !item.isSeparatorItem {
            let action = item.action.map(NSStringFromSelector) ?? ""
            if !names.contains(item.title) && !clipboard.contains(action) { edit.removeItem(item) }
        }
        // Remove separators exposed by deleting the unused text submenus.
        var previousWasSeparator = true
        for item in edit.items {
            if item.isSeparatorItem && previousWasSeparator { edit.removeItem(item) }
            else { previousWasSeparator = item.isSeparatorItem }
        }
        if edit.items.last?.isSeparatorItem == true { edit.removeItem(at:edit.numberOfItems-1) }
    }
    func apply() {
        guard let edit = NSApp?.mainMenu?.items.first(where:{ ["Edit", "编辑"].contains($0.title) })?.submenu else { return }
        apply(to:edit)
    }
}
