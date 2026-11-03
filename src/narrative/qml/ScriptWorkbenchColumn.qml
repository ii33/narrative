import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Frame {
    id: column
    property var t
    property string title: ""
    property string namePrefix: "scriptColumn"
    property bool collapsed: false
    property real expandedWidth: 280
    property real minimumExpandedWidth: 220
    default property alias contents: body.data
    readonly property real effectiveMinimumWidth: collapsed ? 42 : minimumExpandedWidth
    SplitView.preferredWidth: collapsed ? 42 : expandedWidth
    SplitView.minimumWidth: effectiveMinimumWidth
    SplitView.maximumWidth: collapsed ? 42 : Number.POSITIVE_INFINITY
    padding: collapsed ? 2 : 8
    clip: true
    onCollapsedChanged: {
        if (collapsed) { expandedWidth = Math.max(minimumExpandedWidth, width); SplitView.preferredWidth = 42 }
        else SplitView.preferredWidth = expandedWidth
    }
    background: Rectangle { color: column.t.card; border.color: column.t.line; radius: 6 }
    contentItem: Item {
        ColumnLayout {
            anchors.fill: parent
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Label { visible: !column.collapsed; text: column.title; font.bold: true; color: column.t.ink; Layout.fillWidth: true; elide: Text.ElideRight }
                ToolButton {
                    objectName: column.namePrefix + "Toggle"
                    text: column.collapsed ? ">" : "<"
                    implicitWidth: 32
                    ToolTip.visible: hovered
                    ToolTip.text: (column.collapsed ? "展开" : "折叠") + column.title
                    onClicked: column.collapsed = !column.collapsed
                }
            }
            ColumnLayout { id: body; visible: !column.collapsed; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8 }
            Item {
                visible: column.collapsed
                Layout.fillWidth: true; Layout.fillHeight: true
                Label { anchors.centerIn: parent; rotation: 90; text: column.title; color: column.t.muted; font.bold: true }
            }
        }
    }
}
