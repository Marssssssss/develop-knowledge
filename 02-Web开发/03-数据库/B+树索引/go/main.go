// Demo entry point for the B+ tree.
package main

import "fmt"

func main() {
	fmt.Println("=== demo 1: insert 1..20, watch splits propagate ===")
	root := leafNew()
	for k := 1; k <= 20; k++ {
		root = Insert(root, k, fmt.Sprintf("v%d", k))
	}
	visualize(root, "", true)
	fmt.Println()

	fmt.Println("=== demo 2: point search ===")
	for _, k := range []int{3, 11, 20, 99} {
		v, ok := Search(root, k)
		if ok {
			fmt.Printf("  search(%d) -> %q\n", k, v)
		} else {
			fmt.Printf("  search(%d) -> NULL\n", k)
		}
	}
	fmt.Println()

	fmt.Println("=== demo 3: range query (lo=7, hi=15) via sibling chain ===")
	fmt.Print("  ")
	for _, p := range RangeQuery(root, 7, 15) {
		fmt.Printf("%v:%v ", p[0], p[1])
	}
	fmt.Println("\n")

	fmt.Println("=== demo 4: delete with borrow from sibling ===")
	bkeys := []int{5, 8, 1, 7, 3, 12, 9, 14, 6, 11}
	bvals := []string{"v5", "v8", "v1", "v7", "v3", "v12", "v9", "v14", "v6", "v11"}
	root2 := BulkLoad(bkeys, bvals)
	fmt.Println("before delete:")
	visualize(root2, "", true)
	for _, k := range []int{1, 3, 5} {
		root2 = Delete(root2, k)
	}
	fmt.Println("after deleting 1, 3, 5:")
	visualize(root2, "", true)
	fmt.Println()

	fmt.Println("=== demo 5: bulk-load 1..30 (sorted input, O(N)) ===")
	k30 := make([]int, 30)
	v30 := make([]string, 30)
	for i := 0; i < 30; i++ {
		k30[i] = i + 1
		v30[i] = fmt.Sprintf("val%d", i+1)
	}
	root3 := BulkLoad(k30, v30)
	fmt.Print("  root keys:")
	for i := 0; i < root3.nk; i++ {
		fmt.Printf(" %d", root3.keys[i])
	}
	fmt.Println()
	fmt.Print("  range[10..25] -> ")
	for _, p := range RangeQuery(root3, 10, 25) {
		fmt.Printf("%v:%v ", p[0], p[1])
	}
	fmt.Println()

	freeAll(root)
	freeAll(root2)
	freeAll(root3)
}