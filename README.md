# GeoGit: QGIS პროექტის საქაღალდის ვერსიონირება

მთელი პროექტის საქაღალდე (`.qgz`, ვექტორები, რასტრები, სტილები, დოკუმენტები) git-ის კონტროლზე, QGIS-ის პანელიდან, ღილაკებით. დიდი გეო-ფაილები Git LFS-ით ინახება. სერვერად გამოდგება GitHub, Forgejo/Gitea ან ნებისმიერი git სერვერი.

## რას აკეთებს ახლა (0.1.0)
- ვერსიონირების ჩართვა საქაღალდეზე (`.gitignore` და LFS წესები გეო-ფორმატებისთვის)
- შეცვლილი ფაილების სია ტიპებით (ვექტორი, რასტრი, პროექტი, სტილი), შესანახის მონიშვნა
- „შენახვა ვერსიად“ (commit), ცვლილების გაუქმება
- სერვერის მითითება, „გამოქვეყნება“ (push), „მოტანა“ (pull), სერვერიდან ჩამოტვირთვა (clone)
- ბრენჩები
- ისტორია: აღწერა, ავტორი, თარიღი, ვერსიაში შეცვლილი ფაილები
- ქართული და ინგლისური ინტერფეისი

## გეგმა
1. ✅ საფუძველი: git/LFS, ცვლილებები, commit, push/pull
2. ✅ ისტორიის ხე (გრაფი), ფერი ავტორის მიხედვით, ფილტრები, ფაილის ისტორია
3. ✅ ვექტორის diff რუკაზე: დამატებული, შეცვლილი (ძველი საზღვრით), წაშლილი
4. ✅ რასტრის diff: სხვაობის რუკა, შეცვლილი უბნები, swipe-ით შედარება
5. ✅ `.qgz`-ის diff (ფენები, სტილი, ხე, CRS, layout-ები), ფაილების დაბლოკვა (`git lfs lock`)
6. ✅ merge და კონფლიქტების ფანჯარა: GeoPackage-ის შერწყმა ობიექტების და ველების დონეზე

## მოთხოვნები
- QGIS 3.34+ (Qt5 ან Qt6)
- `git`; დიდი ფაილებისთვის `git-lfs` (`sudo apt install git-lfs`)
- GitHub-ისთვის ერთხელ: `gh auth login && gh auth setup-git`

## დეველოპმენტი
```bash
scripts/dev_install.sh                                   # QGIS პროფილში ბმულის შექმნა
python3 -m unittest discover -s tests                    # ბექენდის ტესტები
QT_QPA_PLATFORM=offscreen python3 tests/smoke_qgis.py    # პანელის ტესტი PyQGIS-ში
QT_QPA_PLATFORM=offscreen python3 tests/smoke_diff.py    # ვექტორის ცვლილებები რუკაზე
QT_QPA_PLATFORM=offscreen python3 tests/smoke_raster.py  # რასტრის ცვლილებები და swipe
QT_QPA_PLATFORM=offscreen python3 tests/smoke_project.py # პროექტის ცვლილებები და დაბლოკვის ნიშნები
QT_QPA_PLATFORM=offscreen python3 tests/smoke_merge.py   # pull კონფლიქტით, შერწყმა და დასრულება
```

ლიცენზია: GPL-3.0-or-later
