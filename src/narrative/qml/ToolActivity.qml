import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: activity
    objectName: "assistantToolActivity"
    property var t
    property var tools: []
    property bool expanded: false
    signal userToggled()
    visible: tools.length > 0
    spacing: 3

    function label(name) {
        var labels = {read_source: "查看原文", list_sources: "查看原文列表",
            locate_source: "定位引文", list_source_slices: "查看切片", read_source_slice: "查看切片原文",
            preview_pattern_slices: "预览章节切点", create_pattern_slices: "批量创建切片",
            create_source_slices: "创建切片", list_nodes: "查看节点", get_node: "查看节点详情",
            node_properties: "查看属性", preview_extraction: "核对提取草稿", submit_extraction: "提交自动入库"}
        return labels[name] || name || "工具"
    }

    readonly property string summary: tools.map(function(tool) {
        return tool.display_name || activity.label(tool.tool || "工具")
    }).join("、")
    readonly property string details: tools.map(function(tool) {
        var args = tool.args || ({})
        if (typeof args === "string") {
            try { args = JSON.parse(args) } catch (ignored) {}
        }
        return (tool.display_name || activity.label(tool.tool)) + "（"+(tool.tool||"工具")+"）"
            + (tool.error ? " · " + tool.error : "")
            + "\n参数\n" + (tool.arguments_text!==undefined?tool.arguments_text:JSON.stringify(args,null,2))
            + (tool.arguments_truncated?"\n参数预览已截断；完整参数保留在任务记录。":"")
            + "\n输出\n" + (tool.output_text!==undefined?tool.output_text:tool.summary||"等待工具结果")
            + (tool.output_truncated?"\n输出预览已截断；完整结果保留在任务记录。":"")

    }).join("\n\n")

    ToolButton {
        objectName: "assistantToolToggle"
        Layout.fillWidth: true
        text: (activity.expanded ? "▾ " : "▸ ") + "工具过程（" + activity.tools.length + "） · " + activity.summary
        onClicked: { activity.expanded = !activity.expanded; activity.userToggled() }
        contentItem: Label {
            text: parent.text
            color: activity.t.muted
            font.pixelSize: 10
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }
    }
    AssistantSelectableText {
        objectName: "assistantToolDetails"
        Layout.fillWidth: true
        visible: activity.expanded
        readOnly: true
        selectByMouse: true
        text: activity.expanded ? activity.details : ""
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.Wrap
        color: activity.t.muted
        font.pixelSize: 10
        background: Rectangle { color: activity.t.panelAlt; radius: 4 }
    }
}
