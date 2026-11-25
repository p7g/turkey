#!/bin/sh
# Prototype only: rewrite the line-region constants in Turkey.Heap and
# Turkey.HeapCheck for 2^SHIFT-byte lines (3 to 7). Run from the repository root.
# Usage: scripts/prototype/line-size.sh SHIFT
shift_=$1
bytes=$((1 << shift_)); lines=$((65536 >> shift_)); bitmap=$((lines / 8))
data=$(( (3136 + bitmap + bytes - 1) / bytes * bytes )); [ $data -lt 3200 ] && [ $shift_ -eq 7 ] && data=3200
dataline=$((data >> shift_)); gshift=$((shift_ - 3)); granules=$((bytes / 8))
for f in lib/Turkey/Heap.gob lib/Turkey/HeapCheck.gob; do
  sed -i '' -e "s/^let lineBytes : Int = .*/let lineBytes : Int = $bytes/" \
    -e "s/^let lineShift : Int = .*/let lineShift : Int = $shift_/" \
    -e "s/^let regionLineCount : Int = .*/let regionLineCount : Int = $lines/" \
    -e "s/^let lineGranuleShift : Int = .*/let lineGranuleShift : Int = $gshift/" \
    -e "s/^let lineGranules : Int = .*/let lineGranules : Int = $granules/" \
    -e "s/^let dataLine : Int = .*/let dataLine : Int = $dataline/" -e "s/^let roomyLines : Int = .*/let roomyLines : Int = $((1024 / bytes))/" \
    -e "s/^let regionData : Int = .*/let regionData : Int = $data/" $f
done
echo "lines=$bytes count=$lines data=$data dataLine=$dataline"
