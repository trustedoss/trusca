# Maven POM fixtures

Unmodified POM files downloaded from Maven Central
(`https://repo1.maven.org/maven2/<group path>/<artifact>/<version>/<artifact>-<version>.pom`)
on 2026-09-20. File names are `<groupId>_<artifactId>_<version>.pom`. The POMs are
published metadata; no file was edited.

They are used by `tests/unit/integrations/license_fetcher/test_maven_parent.py` to
exercise parent-POM licence inheritance against real chains:

| Child (declares no licenses) | Chain read | Ancestor that declares |
|---|---|---|
| `org.slf4j:slf4j-api:2.0.2` | `slf4j-parent:2.0.2` | `slf4j-parent` (MIT License) |
| `org.apache.commons:commons-lang3:3.14.0` | `commons-parent:64`, `org.apache:apache:30` | `apache:30` |
| `com.fasterxml.jackson.datatype:jackson-datatype-jdk8:2.15.3` and two siblings | `jackson-modules-java8:2.15.3`, `jackson-base:2.15.3` | `jackson-base` |
| `ch.qos.logback:logback-core:1.4.11` | `logback-parent:1.4.11` | `logback-parent` |

`org.springframework.boot:spring-boot:3.2.0` declares its own license and is the
case where the parent must not be read. `org.slf4j:jul-to-slf4j:2.0.9` is kept as a
second child of the slf4j project.

The children come from the components of `tests/fixtures/sbom/real_cyclonedx_maven_scoped.json`
that carry no license in the SBOM.

## License name spellings

Seven more POMs, downloaded the same way on 2026-09-20, each declaring a license name
that `normalize_spdx_id` did not map before the alias additions. They come from the
same SBOM's components without a license, either as the component's own POM or as the
ancestor it inherits from (`tests/unit/integrations/license_fetcher/test_license_names_real_poms.py`
asserts the name against each file).

| POM | Declared name | Maps to |
|---|---|---|
| `org.hamcrest:hamcrest:2.2` | `BSD License 3` | BSD-3-Clause |
| `org.eclipse.angus:angus-activation-project:2.0.0` | `EDL 1.0` | BSD-3-Clause |
| `com.sun.xml.bind.mvn:jaxb-parent:4.0.2` | `Eclipse Distribution License - v 1.0` | BSD-3-Clause |
| `jakarta.persistence:jakarta.persistence-api:3.1.0` | `Eclipse Public License v. 2.0` | EPL-2.0 |
| `org.junit.jupiter:junit-jupiter:5.10.1` | `Eclipse Public License v2.0` | EPL-2.0 |
| `org.opentest4j:opentest4j:1.3.0` | `The Apache License, Version 2.0` | Apache-2.0 |
| `org.antlr:antlr4-master:4.10.1` | `The BSD License` | left unknown |

`The BSD License` is not mapped: the name does not say whether it is the 2-clause or
the 3-clause text.
