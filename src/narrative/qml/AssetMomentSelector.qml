import QtQuick
import QtQuick.Controls

// Shared selector for asset browsing and validity boundaries in every category.
ComboBox {
    property var moments: []
    property string emptyLabel: "不限"
    textRole: "name"
    model: [{id: "", name: emptyLabel}].concat(moments)
    readonly property string selectedId: currentIndex >= 0 ? model[currentIndex].id : ""
}
