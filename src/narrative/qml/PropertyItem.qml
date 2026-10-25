import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 一条属性：当前披露、有效期、完整披露序列。
// 分类只影响显示位置：点分类标签可以把它移到另一个大类，取值本身不动。
Column {
    id: item

    required property var row        // BrowsePage 组装好的行
    required property var host       // 所属页面，负责跳转与移动分类

    readonly property var t: host.t
    readonly property bool expanded: host.expandedProps[row.key] === true
    // 同一属性可能分在两个大类里，这时说明清楚，避免看起来像少了几条。
    readonly property string splitHint: (row.otherCategories || []).length > 0
        ? "也在「" + (row.otherCategories || []).join("」「") + "」中" : ""

    spacing: 0

    Rectangle {
        id: head
        width: parent.width
        height: 46
        radius: 4
        color: headHover.hovered ? item.t.accentSoft : "transparent"

        HoverHandler { id: headHover }

        // 先声明，后声明的标签才能抢到点击（分类标签、持有者各有自己的目标）。
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: item.host.toggleProperty(item.row.key)
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 8
            anchors.rightMargin: 8
            anchors.topMargin: 4
            anchors.bottomMargin: 4
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: (item.expanded ? "− " : "+ ") + item.row.label
                    color: item.t.ink
                    font.pixelSize: 12
                    Layout.preferredWidth: 92
                    elide: Text.ElideRight
                }
                Label {
                    text: item.row.valueText
                    color: item.row.valueNodeId.length > 0 ? item.t.accent : item.t.body
                    font.pixelSize: 12
                    font.underline: item.row.valueNodeId.length > 0
                    elide: Text.ElideRight
                    Layout.fillWidth: true

                    MouseArea {
                        anchors.fill: parent
                        enabled: item.row.valueNodeId.length > 0
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: item.host.jumpTo(item.row.valueNodeId)
                    }
                }
                Label {
                    text: item.row.scopeText
                    color: item.t.accent
                    font.pixelSize: 10
                }
                Label {
                    visible: item.row.holderId.length > 0
                    text: item.row.holderName
                    color: item.t.accent
                    font.pixelSize: 10
                    font.underline: true
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: item.host.jumpTo(item.row.holderId)
                    }
                }
                // 整条属性一起移：同一属性下的记录通常属于同一个方面。
                Rectangle {
                    objectName: "propertyCategoryChip_" + item.row.key
                    implicitWidth: categoryLabel.implicitWidth + 12
                    implicitHeight: 16
                    radius: 8
                    color: categoryArea.containsMouse ? item.t.accent : item.t.accentSoft
                    Label {
                        id: categoryLabel
                        anchors.centerIn: parent
                        text: item.row.category + (item.row.categorySource === "fallback" ? " ?" : "")
                        color: categoryArea.containsMouse ? "#ffffff" : item.t.accent
                        font.pixelSize: 10
                    }
                    MouseArea {
                        id: categoryArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: item.host.askCategory(item.row.category, item.row.recordIds)
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                visible: item.row.validFrom.length > 0 || item.splitHint.length > 0
                text: [item.row.validFrom, item.row.historyText, item.splitHint]
                    .filter(function (part) { return part.length > 0 }).join(" · ")
                color: item.t.muted
                font.pixelSize: 10
                elide: Text.ElideRight
            }
        }
    }

    Repeater {
        model: item.expanded ? item.row.records : []

        delegate: Column {
            required property var modelData

            width: item.width
            leftPadding: 18
            topPadding: 2
            bottomPadding: 6
            spacing: 2

            RowLayout {
                width: parent.width - parent.leftPadding
                spacing: 6

                Label {
                    text: modelData.kind === "property_retraction"
                        ? "撤回"
                        : modelData.is_current ? "当前" : "历史"
                    color: modelData.is_current ? item.t.ok : item.t.muted
                    font.pixelSize: 10
                    Layout.preferredWidth: 28
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: modelData.kind === "property_retraction"
                        ? "该说法已撤回"
                        : modelData.value_node ? modelData.value_node.name
                        : String(modelData.value === null
                            || modelData.value === undefined ? "（空）" : modelData.value)
                    color: modelData.value_node ? item.t.accent : item.t.body
                    font.pixelSize: 11
                    font.underline: !!modelData.value_node

                    MouseArea {
                        anchors.fill: parent
                        enabled: !!modelData.value_node
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: item.host.jumpTo(modelData.value_node.id)
                    }
                }
                // 单条记录移动：同一条 state 可以分到不同大类。
                Rectangle {
                    implicitWidth: recordCategoryLabel.implicitWidth + 10
                    implicitHeight: 15
                    radius: 7
                    color: recordCategoryArea.containsMouse ? item.t.accent : "transparent"
                    border.color: item.t.line
                    Label {
                        id: recordCategoryLabel
                        anchors.centerIn: parent
                        text: modelData.category
                        color: recordCategoryArea.containsMouse ? "#ffffff" : item.t.muted
                        font.pixelSize: 9
                    }
                    MouseArea {
                        id: recordCategoryArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: item.host.askCategory(modelData.category, [modelData.id])
                    }
                }
            }
            RowLayout {
                width: parent.width - parent.leftPadding
                spacing: 4

                Label {
                    text: "事件"
                    color: item.t.muted
                    font.pixelSize: 10
                }
                Label {
                    text: modelData.event_name || modelData.event_id || "—"
                    color: modelData.event_name ? item.t.accent : item.t.muted
                    font.pixelSize: 10
                    font.underline: !!modelData.event_name
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: item.host.jumpTo(modelData.event_id)
                    }
                }
                Label {
                    text: "→ " + (modelData.valid_to_event || "至今")
                    color: item.t.muted
                    font.pixelSize: 10
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    text: "· 位置 " + (modelData.disclosure ? modelData.disclosure.start : "—")
                        + " · " + (modelData.source || "未标注来源")
                    color: item.t.muted
                    font.pixelSize: 10
                    elide: Text.ElideRight
                }
                Label {
                    visible: !!(modelData.disclosure && modelData.disclosure.source_id)
                    text: "回原文"
                    color: item.t.accent
                    font.pixelSize: 10
                    font.underline: true
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: item.host.openSource(modelData.disclosure.source_id,
                                                        modelData.disclosure.start)
                    }
                }
            }

            RowLayout {
                width: parent.width - parent.leftPadding
                spacing: 4
                visible: (modelData.related || []).length > 0

                Label {
                    text: "关联"
                    color: item.t.muted
                    font.pixelSize: 10
                }
                Repeater {
                    model: modelData.related || []

                    delegate: Label {
                        required property var modelData
                        text: modelData.name
                        color: item.t.accent
                        font.pixelSize: 10
                        font.underline: true
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: item.host.jumpTo(modelData.id)
                        }
                    }
                }
                Item { Layout.fillWidth: true }
            }
            Label {
                width: parent.width - parent.leftPadding
                wrapMode: Text.Wrap
                visible: !!modelData.evidence
                text: modelData.evidence ? "「" + modelData.evidence.quote + "」" : ""
                color: item.t.muted
                font.pixelSize: 10
            }
        }
    }
}
