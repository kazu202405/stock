今銘柄説明の内容いけてるはず？表示されてないねんけど。
原因はフロントが期待しているキー名と、バックエンドが返しているキー名がズレているからとかはある？
もしくは他に原因があるなら教えて


https://shikiho.toyokeizai.net/stocks/1928
（1928はコード）
ここと
①
full xpath
/html/body/div[2]/div/div/div[1]/div/div[2]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[1]/td/text()
xpath
//*[@id="main"]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[1]/td
ここ
②
full xpath
/html/body/div[2]/div/div/div[1]/div/div[2]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[1]/td/text()
xpath
//*[@id="main"]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[2]/td

ってとってこれるかな？コード変わったら要素変わりそう？
ちなみに①のテーマ？が
//*[@id="main"]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[1]/th
/html/body/div[2]/div/div/div[1]/div/div[2]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[1]/th
②のテーマ？が
//*[@id="main"]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[2]/th
/html/body/div[2]/div/div/div[1]/div/div[2]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table/tr[2]/th
ここに入ってて、
テーブルとしては
<div data-v-308d1a5e="" data-v-46394128="" class="shimen-articles"><table data-v-308d1a5e="" class="shimen-articles__table"><tr data-v-308d1a5e=""><th data-v-308d1a5e="">【伸　長】</th> <td data-v-308d1a5e="">前期買収の米国住宅会社が通期寄与。牽引役の米国戸建ては販促費増重いが、単価高水準で着実。高価格帯軸に国内戸建て伸びる。賃貸も下期から利益率改善し順調。都市再開発後退でも連続最高純益。</td></tr><tr data-v-308d1a5e=""><th data-v-308d1a5e="">【積極採用】</th> <td data-v-308d1a5e="">採用強化する建設子会社は33年4月時点で社員工1000人(24年3月末比約3倍)目標。米国で工期短縮やグループでの資材共同購入等原価抑制進める。</td></tr></table> <p data-v-308d1a5e="" class="shimen-articles__data">2025年3集夏号（2025年6月18日発売）
      </p></div>
//*[@id="main"]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table
/html/body/div[2]/div/div/div[1]/div/div[2]/div[3]/div/div[1]/div[1]/div[1]/div[1]/table

ここを取ってきたいって感じ。