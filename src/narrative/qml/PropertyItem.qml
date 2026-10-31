import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 一条属性：当前值、它披露在哪一次、完整披露序列。失效的记录不出现。
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
                    Layout.minimumWidth: 0
                }
                Label {
                    text: item.row.valueText
                    color: item.row.valueNodeId.length > 0 ? item.t.accent : item.t.body
                    font.pixelSize: 12
                    font.underline: item.row.valueNodeId.length > 0
                    elide: Text.ElideRight
                    Layout.minimumWidth: 0
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
                    // 多作用域时会写成「声称/认知/世界」，不限宽就会把分类标签挤出面板。
                    Layout.maximumWidth: 92
                    elide: Text.ElideRight
                    Layout.minimumWidth: 0
                }
                Label {
                    visible: item.row.holderId.length > 0
                    text: item.row.holderName
                    color: item.t.accent
                    font.pixelSize: 10
                    font.underline: true
                    Layout.maximumWidth: 72
                    elide: Text.ElideRight
                    Layout.minimumWidth: 0
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
                // 两个名字合成一个方面：只在真合了的时候给一个入口，不单独占一行。
                Label {
                    visible: item.row.nameIds.length > 1
                    text: "合并…"
                    color: item.t.accent
                    font.pixelSize: 10
                    font.underline: true
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: item.host.askMerge(item.row.nameIds)
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                visible: item.row.disclosedText.length > 0 || item.splitHint.length > 0
                    || (item.row.derivedText || "").length > 0
                // 只说这句是哪一次披露的。失效的不在这里出现，所以也不提。
                text: [item.row.disclosedText, item.row.derivedText || "", item.splitHint]
                    .filter(function (part) { return part.length > 0 }).join(" · ")
                color: item.t.muted
                font.pixelSize: 10
                elide: Text.ElideRight
                Layout.minimumWidth: 0
            }
        }
    }

    // 改名在节点编辑器里（它是「改这个方面」的编辑动作，和更正/失效放一起），
    // 浏览区只负责读，不在这里放编辑入口。
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

                // 失效的不列出来，所以这里也不标「有效」——写出来的都算数。
                // 这一行合了多个属性名时，标出这条记录用的是哪个。
                Label {
                    visible: item.row.names.length > 0
                    text: modelData.property_label || modelData.property_id
                    color: item.t.muted
                    font.pixelSize: 9
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: modelData.kind === "property_retraction"
                        ? "该说法已撤回"
                        : modelData.value_node ? modelData.value_node.name
                        : String(modelData.value === null
                            || modelData.value === undefined ? "（空）" : modelData.value)
                    // 更早的披露仍然算数，但不再是当下状态：压暗，别当第二个当前值看。
                    color: modelData.value_node
                        ? item.t.accent
                        : (modelData.is_latest === false ? item.t.muted : item.t.body)
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
                    // 整组都是同一个大类时不必重复标；只有这条记录被单独挪过才显示。
                    visible: modelData.category !== item.row.category
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
                    visible: modelData.is_latest === false || modelData.scope !== "world"
                    text: modelData.is_latest === false ? "较早" : ""
                    color: item.t.muted
                    font.pixelSize: 9
                }
                Label {
                    visible: modelData.scope !== "world"
                    text: (modelData.scope === "claim" ? "声称" : "认知")
                        + (modelData.holder_name ? "·" + modelData.holder_name : "")
                    color: item.t.muted
                    font.pixelSize: 9
                }
                Label {
                    Layout.fillWidth: true
                    // 只写披露在哪个事件；不再声称「到某处为止」，那是算出来的。
                    text: modelData.event_name || modelData.event_id || "—"
                    color: item.t.accent
                    font.pixelSize: 10
                    font.underline: !!modelData.event_name
                    elide: Text.ElideRight
                    Layout.minimumWidth: 0
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: item.host.jumpTo(modelData.event_id)
                    }
                }
            }
            RowLayout {
                width: parent.width - parent.leftPadding
                spacing: 4

                Label {
                    Layout.fillWidth: true
                    // 跨章时位置数字不可比，所以带上章节名。
                    text: (modelData.source_title || "未标注章节")
                        + " · 位置 " + (modelData.disclosure ? modelData.disclosure.start : "—")
                        + (modelData.source ? " · " + modelData.source : "")
                    color: item.t.muted
                    font.pixelSize: 10
                    elide: Text.ElideMiddle
                    Layout.minimumWidth: 0
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
